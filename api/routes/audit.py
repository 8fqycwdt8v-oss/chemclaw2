"""Audit routes — compliance trail endpoints."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_admin_user, get_current_user
from api.db.connection import get_db
from api.db.queries.audit import get_session_replay, list_overrides
from api.db.queries.rate_limit import rate_limit

logger = logging.getLogger(__name__)

router = APIRouter()

_AUDIT_ADMIN = [Depends(get_admin_user), Depends(rate_limit("audit-overrides", 30))]
_AUDIT_REDACTIONS = [Depends(get_admin_user), Depends(rate_limit("audit-redactions", 30))]
_AUDIT_SESSION = [Depends(rate_limit("audit-session", 20))]


@router.get("/api/audit/overrides", dependencies=_AUDIT_ADMIN)
async def audit_overrides(
    user_id_filter: str | None = Query(None, alias="user_id"),
    session_id: str | None = Query(None),
    gate_name: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List agent override records. Admin only. Rate limit: 30 per 60 s per admin user."""
    overrides = await list_overrides(
        db,
        user_id=user_id_filter,
        session_id=session_id,
        gate_name=gate_name,
        limit=limit,
    )
    return {"overrides": overrides}


@router.get("/api/audit/redactions", dependencies=_AUDIT_REDACTIONS)
async def audit_redactions(
    session_id: str | None = Query(None),
    user_id_filter: str | None = Query(None, alias="user_id"),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """List tool-call redaction events (gate_name='redaction'). Admin only."""
    overrides = await list_overrides(
        db,
        user_id=user_id_filter,
        session_id=session_id,
        gate_name="redaction",
        limit=limit,
    )
    return {"redactions": overrides}


@router.get("/api/audit/sessions/{session_id}", dependencies=_AUDIT_SESSION)
async def audit_session_replay(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Return session entries for the caller's own sessions.

    Auth: any authenticated user; project_key is scoped to the calling user so
    they can only retrieve sessions they own. Rate limit: 20 per 60 s per user.
    """
    project_key = f"chemclaw2:{user_id}"
    entries = await get_session_replay(db, session_id=session_id, project_key=project_key)
    return {"entries": entries}
