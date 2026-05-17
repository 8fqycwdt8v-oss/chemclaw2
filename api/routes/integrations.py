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
from api.db.queries.rate_limit import pg_rate_limit

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
    Rate limit: 100 per 60 s keyed on "eln-webhook".
    """
    limited = await pg_rate_limit(db, "eln-webhook", 100, 60_000)
    if limited["limited"]:
        logger.warning("eln_webhook_rate_limited")
        raise HTTPException(status_code=429, detail="Too many requests")

    secret = os.environ.get("ELN_WEBHOOK_SECRET", "")
    if not secret:
        raise HTTPException(status_code=400, detail="ELN webhook not configured")

    body_bytes = await request.body()

    sig_header = request.headers.get("X-ELN-Signature", "")
    expected_sig = "sha256=" + hmac.new(
        secret.encode(), body_bytes, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected_sig, sig_header):
        logger.warning("eln_webhook_invalid_signature")
        raise HTTPException(status_code=401, detail="Invalid signature")

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


@router.post("/api/integrations/documents")
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
    limited = await pg_rate_limit(db, f"doc-upload:{user_id}", 5, 60_000)
    if limited["limited"]:
        logger.warning("doc_upload_rate_limited user=%s", user_id)
        raise HTTPException(status_code=429, detail="Too many requests")

    content_type = file.content_type or ""
    if content_type not in _ALLOWED_MIME_TYPES:
        raise HTTPException(
            status_code=415,
            detail=f"Unsupported file type '{content_type}'. Allowed: pdf, plain text, markdown.",
        )

    content = await file.read()
    if len(content) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File exceeds 10 MB limit")

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

    fact_id, _ = await upsert_external_fact(
        db,
        source_type="document",
        source_id=source_id,
        payload={"filename": file.filename, "content_type": content_type},
        content_text=text[:500_000],
        fetched_by=user_id,
    )

    title: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if stripped:
            title = stripped[:200]
            break

    if title:
        try:
            await upsert_paper(
                db,
                url=f"upload:{source_id}",
                title=title,
                created_by=user_id,
            )
        except Exception:
            logger.exception(
                "doc_upload_upsert_paper_failed source_id=%s", source_id
            )

    return {"fact_id": fact_id, "chars": len(text), "title": title}
