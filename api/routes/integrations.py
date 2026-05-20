"""External integration routes — ELN webhook and document upload."""
from __future__ import annotations

import hashlib
import hmac
import io
import json
import logging
import os
from typing import Any

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.db.connection import get_db
from api.db.queries.compounds import insert_compound
from api.db.queries.knowledge import upsert_external_fact, upsert_paper
from api.db.queries.rate_limit import pg_rate_limit, rate_limit
from api.db.queries.wiki_write import upsert_wiki_page
from api.embeddings import embed_texts
from api.integrations.document_enrichment import (
    extract_doi,
    fetch_crossref_metadata,
    first_nonempty_line,
    slugify_doi,
)

logger = logging.getLogger(__name__)

router = APIRouter()

_ALLOWED_MIME_TYPES = {"application/pdf", "text/plain", "text/markdown"}
_MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB


class ELNWebhookBody(BaseModel):
    event: str
    experiment_id: str
    data: dict[str, Any]


@router.post("/api/integrations/eln/webhook")
async def eln_webhook(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Receive an ELN event and persist it as an external fact.

    Auth: HMAC-SHA256 of request body using ELN_WEBHOOK_SECRET.
    Rate limit applied AFTER signature verification so unauthenticated callers
    cannot exhaust the rate-limit budget.
    """
    secret = os.environ.get("ELN_WEBHOOK_SECRET", "")
    if not secret:
        logger.warning("eln_webhook_not_configured")
        raise HTTPException(status_code=503, detail="ELN webhook not configured")

    body_bytes = await request.body()

    sig_header = request.headers.get("X-ELN-Signature", "")
    expected_sig = "sha256=" + hmac.new(
        secret.encode(), body_bytes, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected_sig, sig_header):
        logger.warning("eln_webhook_invalid_signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

    limited = await pg_rate_limit(db, "eln-webhook", 100, 60_000)
    if limited["limited"]:
        logger.warning("eln_webhook_rate_limited")
        raise HTTPException(status_code=429, detail="Too many requests")

    body = ELNWebhookBody.model_validate_json(body_bytes)

    fact_id, _ = await upsert_external_fact(
        db,
        source_type="eln",
        source_id=body.experiment_id,
        payload=body.data,
        content_text=json.dumps(body.data)[:10_000],
        fetched_by="eln-webhook",
    )

    # Use fresh sessions for each SMILES insert so a failure on one compound
    # does not leave the request-scoped session in an error state.
    from api.db.connection import async_session_factory
    for key in ("smiles", "product_smiles", "reactant_smiles"):
        smiles = body.data.get(key)
        if smiles and isinstance(smiles, str) and smiles.strip():
            try:
                if async_session_factory is not None:
                    async with async_session_factory() as cdb:
                        async with cdb.begin():
                            await insert_compound(cdb, smiles.strip(), created_by="eln-webhook")
            except Exception:
                logger.exception(
                    "eln_webhook_insert_compound_failed",
                    extra={"key": key, "experiment_id": body.experiment_id},
                )

    return {"ok": True, "fact_id": fact_id}


@router.post("/api/integrations/documents", dependencies=[Depends(rate_limit("doc-upload", 5))])
async def upload_document(
    file: UploadFile = File(...),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Upload a PDF, plain-text, or Markdown document and persist it as an external fact.

    Auth: Bearer JWT (get_current_user).
    Rate limit: 5 per 60 s per user.
    Max size: 10 MB.
    """
    content_type = file.content_type or ""
    if content_type not in _ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{content_type}'. Allowed: pdf, plain text, markdown.",
        )

    # Read in chunks to enforce size limit before buffering the whole file.
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await file.read(65536)
        if not chunk:
            break
        total += len(chunk)
        if total > _MAX_UPLOAD_BYTES:
            raise HTTPException(status_code=413, detail="File exceeds 10 MB limit")
        chunks.append(chunk)
    content = b"".join(chunks)

    # Validate magic bytes for PDFs — Content-Type header alone is attacker-controlled.
    if content_type == "application/pdf" and not content.startswith(b"%PDF"):
        raise HTTPException(status_code=415, detail="File does not appear to be a valid PDF")

    if content_type == "application/pdf":
        try:
            import pypdf  # optional dep — import inside function for graceful failure

            reader = pypdf.PdfReader(io.BytesIO(content))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except ImportError:
            raise HTTPException(status_code=422, detail="PDF support not available")
        except Exception:
            logger.exception("doc_upload_pdf_parse_failed filename=%s", file.filename)
            raise HTTPException(status_code=422, detail="Failed to parse PDF")
    else:
        text = content.decode("utf-8", errors="replace")

    source_id = hashlib.sha256(content).hexdigest()
    doi = extract_doi(text)

    # Enrich with CrossRef metadata when a DOI is present. Network call is
    # best-effort: a CrossRef miss or timeout falls back to the
    # first-non-empty-line heuristic — the upload still succeeds.
    metadata: dict[str, Any] = {}
    if doi:
        crossref = await fetch_crossref_metadata(doi)
        if crossref:
            metadata = crossref

    title = metadata.get("title") or first_nonempty_line(text)
    abstract = metadata.get("abstract")

    fact_id, _ = await upsert_external_fact(
        db,
        source_type="document",
        source_id=source_id,
        payload={
            "filename": file.filename,
            "content_type": content_type,
            "doi": doi,
            "title": title,
            "abstract": abstract,
            "authors": metadata.get("authors") or [],
            "container_title": metadata.get("container_title"),
            "published_year": metadata.get("published_year"),
        },
        content_text=text[:500_000],
        fetched_by=user_id,
    )

    if title:
        try:
            await upsert_paper(
                db,
                url=(f"https://doi.org/{doi}" if doi else f"upload:{source_id}"),
                title=title,
                doi=doi,
                abstract=abstract,
                content_text=text[:500_000] if not abstract else None,
                created_by=user_id,
            )
        except Exception:
            logger.exception(
                "doc_upload_upsert_paper_failed source_id=%s", source_id
            )

    # Wiki page draft (needs_review=True so the curator queue surfaces it).
    # Idempotent: stable slug per (doi or content hash) means re-uploading
    # the same paper updates rather than duplicates.
    wiki_slug: str | None = None
    if title:
        slug_base = slugify_doi(doi) if doi else f"doc-{source_id[:12]}"
        # Truncate to wiki slug length budget; the regex requires alnum endpoints.
        wiki_slug = slug_base[:80].rstrip("-") or f"doc-{source_id[:12]}"
        try:
            authors_str = ", ".join(metadata.get("authors") or [])
            container = metadata.get("container_title")
            year = metadata.get("published_year")
            body_lines = [
                f"# {title}",
                "",
                f"**Source:** {file.filename or 'uploaded document'}",
            ]
            if doi:
                body_lines.append(f"**DOI:** [{doi}](https://doi.org/{doi})")
            if authors_str:
                body_lines.append(f"**Authors:** {authors_str}")
            if container:
                body_lines.append(f"**Journal:** {container}")
            if year:
                body_lines.append(f"**Year:** {year}")
            if abstract:
                body_lines.extend(["", "## Abstract", "", abstract])
            body_lines.extend(["", "## Extracted text (excerpt)", "", text[:5000]])
            wiki_text = "\n".join(body_lines)
            await upsert_wiki_page(
                db,
                slug=wiki_slug,
                title=title,
                content={"type": "doc", "content": []},
                content_text=wiki_text,
                created_by=user_id,
                citations=[],
                embed_fn=embed_texts,
                project="papers",
                needs_review=True,
            )
        except Exception:
            logger.exception(
                "doc_upload_upsert_wiki_failed source_id=%s slug=%s",
                source_id, wiki_slug,
            )
            wiki_slug = None

    return {
        "fact_id": fact_id,
        "chars": len(text),
        "title": title,
        "doi": doi,
        "wiki_slug": wiki_slug,
        "abstract": abstract,
    }
