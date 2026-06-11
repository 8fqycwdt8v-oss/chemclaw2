"""External integration routes — ELN webhook and document upload."""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.db.connection import get_db
from api.db.queries.compounds import insert_compound
from api.db.queries.investigations import get_or_create_corpus_investigation
from api.db.queries.knowledge import upsert_external_fact
from api.db.queries.rate_limit import pg_rate_limit, rate_limit
from api.integrations.extractors import (
    PDF,
    SUPPORTED_CONTENT_TYPES,
    ZIP_CONTENT_TYPES,
    ExtractionError,
    UnsupportedContentType,
)
from api.integrations.ingest import ingest_document

logger = logging.getLogger(__name__)

router = APIRouter()

_MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
# Magic bytes per container family — the Content-Type header is attacker-
# controlled, so PDFs must start with %PDF and OOXML (docx/pptx/xlsx) files
# must be ZIP archives (PK\x03\x04).
_PDF_MAGIC = b"%PDF"
_ZIP_MAGIC = b"PK\x03\x04"


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
                    async with async_session_factory() as cdb, cdb.begin():
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
    extract: Literal["basic", "full"] = Query(
        "basic",
        description=(
            "'basic' (default) extracts text, detects DOI, and fetches "
            "CrossRef metadata. 'full' additionally runs LLM passes to pull "
            "compound mentions + citations (resolved to SMILES via PubChem) "
            "and to extract knowledge-graph facts/evidence + hypotheses into "
            "a per-user corpus investigation. 'full' adds latency and LLM "
            "token cost."
        ),
    ),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Upload a document (PDF / text / Markdown / HTML / docx / pptx / xlsx)
    and persist it across the knowledge surfaces.

    Auth: Bearer JWT (get_current_user).
    Rate limit: 5 per 60 s per user.
    Max size: 10 MB.

    HTTP concerns (size, magic bytes, status mapping) live here; the actual
    extraction + persistence is the shared `ingest_document` path, also used by
    the drive-sync worker.
    """
    content_type = file.content_type or ""
    if content_type not in SUPPORTED_CONTENT_TYPES:
        raise HTTPException(
            status_code=415,
            detail=(
                f"Unsupported file type '{content_type}'. Allowed: PDF, plain "
                "text, Markdown, HTML, Word (docx), PowerPoint (pptx), Excel (xlsx)."
            ),
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

    # Magic-byte validation — the Content-Type header alone is attacker-controlled.
    if content_type == PDF and not content.startswith(_PDF_MAGIC):
        raise HTTPException(status_code=415, detail="File does not appear to be a valid PDF")
    if content_type in ZIP_CONTENT_TYPES and not content.startswith(_ZIP_MAGIC):
        raise HTTPException(
            status_code=415, detail="File does not appear to be a valid Office document"
        )

    # In full mode, anchor extracted knowledge to a per-user "uploads" corpus
    # investigation so the facts/hypotheses are queryable as one thread.
    investigation_id: str | None = None
    if extract == "full":
        investigation_id = await get_or_create_corpus_investigation(
            db,
            title="Document uploads",
            objective="Knowledge extracted from documents uploaded via the API.",
            created_by=user_id,
        )

    try:
        return await ingest_document(
            db,
            content=content,
            filename=file.filename,
            content_type=content_type,
            user_id=user_id,
            extract=extract,
            extract_kg=(extract == "full"),
            investigation_id=investigation_id,
        )
    except UnsupportedContentType:
        raise HTTPException(status_code=415, detail=f"Unsupported file type '{content_type}'") from None
    except ExtractionError:
        logger.exception("doc_upload_extract_failed filename=%s", file.filename)
        raise HTTPException(status_code=422, detail="Failed to parse document") from None
