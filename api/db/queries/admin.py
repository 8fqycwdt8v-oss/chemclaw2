"""Admin queries — tool_permissions and eval_runs tables."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def list_tool_permissions(
    db: AsyncSession,
    scope: str | None = None,
    scope_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return tool permission rows, optionally filtered by scope and/or scope_id."""
    clauses = []
    params: dict[str, Any] = {}
    if scope is not None:
        clauses.append("scope = :scope")
        params["scope"] = scope
    if scope_id is not None:
        clauses.append("scope_id = :scope_id")
        params["scope_id"] = scope_id
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    result = await db.execute(
        text(f"SELECT * FROM tool_permissions {where} ORDER BY scope, scope_id, tool_name"),
        params,
    )
    return [dict(row._mapping) for row in result.fetchall()]


async def upsert_tool_permission(
    db: AsyncSession,
    scope: str,
    scope_id: str,
    tool_name: str,
    mode: str,
    updated_by: str,
) -> str:
    """Insert or update a tool permission. Returns the row id as text."""
    async with db.begin():
        result = await db.execute(
            text("""
                INSERT INTO tool_permissions (scope, scope_id, tool_name, mode, updated_by)
                VALUES (:scope, :scope_id, :tool_name, :mode, :updated_by)
                ON CONFLICT (scope, scope_id, tool_name) DO UPDATE SET
                    mode = EXCLUDED.mode,
                    updated_by = EXCLUDED.updated_by,
                    updated_at = now()
                RETURNING id::text
            """),
            {
                "scope": scope,
                "scope_id": scope_id,
                "tool_name": tool_name,
                "mode": mode,
                "updated_by": updated_by,
            },
        )
        row = result.one()
        return row[0]


async def delete_tool_permission(
    db: AsyncSession,
    permission_id: str,
    updated_by: str,
) -> bool:
    """Delete a tool permission by id. Returns True if a row was deleted."""
    async with db.begin():
        result = await db.execute(
            text("DELETE FROM tool_permissions WHERE id = :id::uuid RETURNING id"),
            {"id": permission_id},
        )
        found = result.one_or_none() is not None
        if not found:
            logger.info("delete_tool_permission_not_found: id=%s by=%s", permission_id, updated_by)
        return found


async def list_eval_runs(db: AsyncSession, limit: int = 20) -> list[dict[str, Any]]:
    """Return the most recent eval runs ordered by started_at DESC."""
    result = await db.execute(
        text("SELECT * FROM eval_runs ORDER BY started_at DESC LIMIT :lim"),
        {"lim": limit},
    )
    return [dict(row._mapping) for row in result.fetchall()]


async def get_eval_run(db: AsyncSession, run_id: str) -> dict[str, Any] | None:
    """Return a single eval run by UUID, or None if not found."""
    result = await db.execute(
        text("SELECT * FROM eval_runs WHERE id = :id::uuid"),
        {"id": run_id},
    )
    row = result.one_or_none()
    return dict(row._mapping) if row else None


async def get_campaign_queue_depth(db: AsyncSession) -> int:
    result = await db.execute(
        text("SELECT COUNT(*) FROM synthesis_campaigns WHERE status = 'running'")
    )
    return result.scalar_one()


async def get_wiki_backlog_depth(db: AsyncSession) -> int:
    result = await db.execute(
        text("SELECT COUNT(*) FROM wiki_pages WHERE needs_review = true AND archived = false")
    )
    return result.scalar_one()


async def get_pending_step_count(db: AsyncSession) -> int:
    result = await db.execute(
        text("SELECT COUNT(*) FROM campaign_steps WHERE status = 'pending'")
    )
    return result.scalar_one()
