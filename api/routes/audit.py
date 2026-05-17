"""Audit routes — compliance trail endpoints."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_admin_user, get_current_user
from api.db.connection import get_db
from api.db.queries.audit import get_session_replay, list_overrides
from api.db.queries.rate_limit import pg_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get("/api/audit/overrides")
async def audit_overrides(
    user_id_filter: str | None = Query(None, alias="user_id"),
    session_id: str | None = Query(None),
    gate_name: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    admin_id: str = Depends(get_admin_user),
) -> dict[str, Any]:
    """List agent override records. Admin only.

    Rate limit: 30 per 60 s per admin user.
    """
    limited = await pg_rate_limit(db, f"audit-overrides:{admin_id}", 30, 60_000)
    if limited["limited"]:
        logger.warning("audit_overrides_rate_limited admin=%s", admin_id)
        raise HTTPException(status_code=429, detail="Too many requests")

    overrides = await list_overrides(
        db,
        user_id=user_id_filter,
        session_id=session_id,
        gate_name=gate_name,
        limit=limit,
    )
    return {"overrides": overrides}


@router.get("/api/audit/redactions")
async def audit_redactions(
    session_id: str | None = Query(None),
    user_id_filter: str | None = Query(None, alias="user_id"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    admin_id: str = Depends(get_admin_user),
) -> dict[str, Any]:
    """List tool-call redaction events (gate_name='redaction'). Admin only."""
    limited = await pg_rate_limit(db, f"audit-redactions:{admin_id}", 30, 60_000)
    if limited["limited"]:
        logger.warning("audit_redactions_rate_limited admin=%s", admin_id)
        raise HTTPException(status_code=429, detail="Too many requests")
    overrides = await list_overrides(
        db,
        user_id=user_id_filter,
        session_id=session_id,
        gate_name="redaction",
        limit=limit,
    )
    return {"redactions": overrides}


@router.get("/api/audit/sessions/{session_id}")
async def audit_session_replay(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Return session entries for the caller's own sessions.

    Auth: any authenticated user; project_key is scoped to the calling user so
    they can only retrieve sessions they own.
    Rate limit: 20 per 60 s per user.
    """
    limited = await pg_rate_limit(db, f"audit-session:{user_id}", 20, 60_000)
    if limited["limited"]:
        logger.warning("audit_session_rate_limited user=%s", user_id)
        raise HTTPException(status_code=429, detail="Too many requests")

    project_key = f"chemclaw2:{user_id}"
    entries = await get_session_replay(db, session_id=session_id, project_key=project_key)
    return {"entries": entries}
