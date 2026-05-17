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
