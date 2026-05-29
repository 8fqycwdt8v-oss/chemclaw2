"""Admin routes — tool-permissions CRUD, eval-runs listing, extended health."""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_admin_user
from api.db.connection import get_db
from api.db.queries.admin import (
    delete_tool_permission,
    get_campaign_queue_depth,
    get_eval_run,
    get_pending_step_count,
    get_wiki_backlog_depth,
    list_eval_runs,
    list_tool_permissions,
    upsert_tool_permission,
)
from api.db.queries.rate_limit import rate_limit

logger = logging.getLogger(__name__)

router = APIRouter()

_EVAL_PREFIX = "/api/admin/eval"

# Admin dep listed before rate-limit so a non-admin sees 403 (not 429).
# FastAPI runs `dependencies=[]` in order and dedupes by callable, so the
# route function's `admin_id = Depends(get_admin_user)` reuses the same call.
_ADMIN_READ = [Depends(get_admin_user), Depends(rate_limit("admin-read", 60))]
_ADMIN_WRITE = [Depends(get_admin_user), Depends(rate_limit("admin-write", 30))]
_ADMIN_HEALTH = [Depends(get_admin_user), Depends(rate_limit("admin-health", 20))]


class ToolPermissionBody(BaseModel):
    scope: Literal["user", "project", "org"]
    scope_id: str
    tool_name: str
    mode: Literal["allow", "ask", "deny"]


@router.get("/api/admin/tool-permissions", dependencies=_ADMIN_READ)
async def get_tool_permissions(
    scope: str | None = Query(None),
    scope_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
):
    permissions = await list_tool_permissions(db, scope=scope, scope_id=scope_id)
    return {"permissions": permissions}


@router.post("/api/admin/tool-permissions", dependencies=_ADMIN_WRITE)
async def create_tool_permission(
    body: ToolPermissionBody,
    db: AsyncSession = Depends(get_db),
    admin_id: str = Depends(get_admin_user),
):
    permission_id = await upsert_tool_permission(
        db,
        scope=body.scope,
        scope_id=body.scope_id,
        tool_name=body.tool_name,
        mode=body.mode,
        updated_by=admin_id,
    )
    logger.info(
        "tool_permission_upserted: id=%s scope=%s scope_id=%s tool=%s mode=%s by=%s",
        permission_id, body.scope, body.scope_id, body.tool_name, body.mode, admin_id,
    )
    return {"id": permission_id}


@router.delete("/api/admin/tool-permissions/{permission_id}", dependencies=_ADMIN_WRITE)
async def remove_tool_permission(
    permission_id: str,
    db: AsyncSession = Depends(get_db),
    admin_id: str = Depends(get_admin_user),
):
    deleted = await delete_tool_permission(db, permission_id, updated_by=admin_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Permission not found")
    return {"ok": True}


@router.get(_EVAL_PREFIX, dependencies=_ADMIN_READ)
async def list_eval(
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    runs = await list_eval_runs(db, limit=limit)
    return {"runs": runs}


@router.get(_EVAL_PREFIX + "/{run_id}", dependencies=_ADMIN_READ)
async def get_eval(
    run_id: str,
    db: AsyncSession = Depends(get_db),
):
    run = await get_eval_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Eval run not found")
    return run


@router.get("/api/admin/health", dependencies=_ADMIN_HEALTH)
async def admin_health(
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Extended health check — campaign queue depth, pending steps, wiki review backlog."""
    try:
        running_campaigns = await get_campaign_queue_depth(db)
    except Exception:
        logger.exception("admin_health_campaign_queue_error")
        running_campaigns = -1
    try:
        wiki_needs_review = await get_wiki_backlog_depth(db)
    except Exception:
        logger.exception("admin_health_wiki_backlog_error")
        wiki_needs_review = -1
    try:
        pending_step_count = await get_pending_step_count(db)
    except Exception:
        logger.exception("admin_health_pending_steps_error")
        pending_step_count = -1
    return {
        "status": "ok",
        "running_campaigns": running_campaigns,
        "pending_campaign_steps": pending_step_count,
        "wiki_needs_review": wiki_needs_review,
    }


@router.post("/api/admin/drive-sync/run", dependencies=_ADMIN_WRITE)
async def trigger_drive_sync(admin_id: str = Depends(get_admin_user)) -> dict:
    """Force a SharePoint/OneDrive delta sync now (e.g. the first backfill).

    Runs synchronously and returns the run summary. Returns 503 when Microsoft
    Graph isn't configured. Admin-only: anything that drives ingestion is
    admin-write per CLAUDE.md security rule 3.
    """
    from api.db.connection import async_session_factory
    from api.workers.sync_worker import run_sync_once

    if async_session_factory is None:
        raise HTTPException(status_code=503, detail="Database not initialised")
    logger.info("admin_drive_sync_triggered admin=%s", admin_id)
    result = await run_sync_once(async_session_factory)
    if result.get("status") == "skipped":
        raise HTTPException(status_code=503, detail="Drive sync not configured")
    return result
