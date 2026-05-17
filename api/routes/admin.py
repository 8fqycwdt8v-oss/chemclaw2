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
    get_eval_run,
    list_eval_runs,
    list_tool_permissions,
    upsert_tool_permission,
)
from api.db.queries.rate_limit import pg_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter()

_EVAL_PREFIX = "/api/admin/eval"


class ToolPermissionBody(BaseModel):
    scope: Literal["user", "project", "org"]
    scope_id: str
    tool_name: str
    mode: Literal["allow", "ask", "deny"]


@router.get("/api/admin/tool-permissions")
async def get_tool_permissions(
    scope: str | None = Query(None),
    scope_id: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    admin_id: str = Depends(get_admin_user),
):
    permissions = await list_tool_permissions(db, scope=scope, scope_id=scope_id)
    return {"permissions": permissions}


@router.post("/api/admin/tool-permissions")
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


@router.delete("/api/admin/tool-permissions/{permission_id}")
async def remove_tool_permission(
    permission_id: str,
    db: AsyncSession = Depends(get_db),
    admin_id: str = Depends(get_admin_user),
):
    deleted = await delete_tool_permission(db, permission_id, updated_by=admin_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Permission not found")
    return {"ok": True}


@router.get(_EVAL_PREFIX)
async def list_eval(
    limit: int = Query(default=20, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    admin_id: str = Depends(get_admin_user),
):
    runs = await list_eval_runs(db, limit=limit)
    return {"runs": runs}


@router.get(_EVAL_PREFIX + "/{run_id}")
async def get_eval(
    run_id: str,
    db: AsyncSession = Depends(get_db),
    admin_id: str = Depends(get_admin_user),
):
    run = await get_eval_run(db, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Eval run not found")
    return run


@router.get("/api/admin/health")
async def admin_health(
    db: AsyncSession = Depends(get_db),
    admin_id: str = Depends(get_admin_user),
) -> dict:
    """Extended health check — worker status, queue depths, wiki backlog."""
    from sqlalchemy import text
    limited = await pg_rate_limit(db, f"admin-health:{admin_id}", 20, 60_000)
    if limited["limited"]:
        raise HTTPException(status_code=429, detail="Too many requests")
    try:
        campaign_queue = await db.execute(
            text("SELECT COUNT(*) FROM campaigns WHERE status = 'running'")
        )
        running_campaigns = campaign_queue.scalar_one()
    except Exception:
        logger.exception("admin_health_campaign_queue_error")
        running_campaigns = -1
    try:
        wiki_backlog = await db.execute(
            text("SELECT COUNT(*) FROM wiki_pages WHERE needs_review = true AND archived = false")
        )
        wiki_needs_review = wiki_backlog.scalar_one()
    except Exception:
        logger.exception("admin_health_wiki_backlog_error")
        wiki_needs_review = -1
    try:
        pending_steps = await db.execute(
            text("SELECT COUNT(*) FROM campaign_steps WHERE status = 'pending'")
        )
        pending_step_count = pending_steps.scalar_one()
    except Exception:
        logger.exception("admin_health_pending_steps_error")
        pending_step_count = -1
    return {
        "status": "ok",
        "running_campaigns": running_campaigns,
        "pending_campaign_steps": pending_step_count,
        "wiki_needs_review": wiki_needs_review,
    }
