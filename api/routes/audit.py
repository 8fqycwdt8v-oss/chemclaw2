"""Audit routes — compliance trail endpoints."""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_admin_user, get_current_user
from api.db.connection import get_db
from api.db.queries.audit import get_session_replay, list_overrides
from api.db.queries.rate_limit import rate_limit
from api.db.queries.wiki_read import get_wiki_page
from api.db.queries.wiki_temporal import list_wiki_revisions

logger = logging.getLogger(__name__)

router = APIRouter()

_AUDIT_ADMIN = [Depends(get_admin_user), Depends(rate_limit("audit-overrides", 30))]
_AUDIT_REDACTIONS = [Depends(get_admin_user), Depends(rate_limit("audit-redactions", 30))]
_AUDIT_SESSION = [Depends(rate_limit("audit-session", 20))]
_AUDIT_WIKI = [Depends(get_admin_user), Depends(rate_limit("audit-wiki", 30))]


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


@router.get("/api/audit/wiki/{slug}", dependencies=_AUDIT_WIKI)
async def audit_wiki_page(
    slug: str,
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
) -> dict[str, Any]:
    """Return the full audit trail for a wiki page. Admin only.

    Includes the current page metadata (id, version, created_by,
    updated_by, maturity, archived, valid_from, valid_to) plus the
    complete revision list ordered newest-first. Pair with the existing
    GET /api/wiki/{slug}/revisions/{version} to fetch full revision
    bodies when a compliance review needs to diff specific versions.

    Spec §3.8 "compliance reproducibility": the page's own metadata
    (versioning + bi-temporal columns) lives next to the per-version
    history in one response, so a regulatory auditor doesn't have to
    cross-reference two endpoints to reconstruct what was on the page
    at a given point in time.
    """
    page = await get_wiki_page(db, slug, include_archived=True)
    if not page:
        raise HTTPException(status_code=404, detail=f"Wiki page '{slug}' not found")
    revisions = await list_wiki_revisions(db, page["id"], limit=limit)
    return {
        "page": {
            "id": page["id"],
            "slug": page["slug"],
            "title": page["title"],
            "version": page["version"],
            "created_by": page["created_by"],
            "updated_by": page.get("updated_by"),
            "created_at": page.get("created_at"),
            "updated_at": page.get("updated_at"),
            "needs_review": page.get("needs_review"),
            "archived": page.get("archived"),
            "maturity": page.get("maturity"),
            "valid_from": page.get("valid_from"),
            "valid_to": page.get("valid_to"),
        },
        "revisions": revisions,
        "revision_count": len(revisions),
    }
