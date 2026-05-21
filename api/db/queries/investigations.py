"""Queries for the `investigations` table — Phase B long-horizon research threads.

An investigation groups world-model entries + hypotheses under a single
open-ended objective. Owner-scoped via `created_by`; every UPDATE/DELETE
predicates on user_id per CLAUDE.md §code-conventions.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


_VALID_STATUSES = {"active", "paused", "complete"}


async def create_investigation(
    db: AsyncSession,
    title: str,
    objective: str,
    created_by: str,
    session_id: str | None = None,
) -> str:
    """Create a new investigation. Returns the new id."""
    async with db.begin():
        result = await db.execute(
            text("""
                INSERT INTO investigations (session_id, title, objective, created_by)
                VALUES (:sid, :title, :obj, :uid)
                RETURNING id::text
            """),
            {"sid": session_id, "title": title, "obj": objective, "uid": created_by},
        )
        return result.scalar_one()


async def get_investigation(
    db: AsyncSession,
    investigation_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Return the investigation row if it exists AND belongs to user_id."""
    result = await db.execute(
        text("""
            SELECT id::text, session_id, title, objective, status,
                   created_by, created_at, updated_at
            FROM investigations
            WHERE id = CAST(:iid AS uuid)
              AND created_by = :uid
        """),
        {"iid": investigation_id, "uid": user_id},
    )
    row = result.one_or_none()
    return dict(row._mapping) if row else None


async def list_investigations(
    db: AsyncSession,
    user_id: str,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List investigations owned by user_id, newest-updated first."""
    safe_limit = min(max(1, limit), 200)
    params: dict[str, Any] = {"uid": user_id, "lim": safe_limit}
    status_clause = ""
    if status is not None:
        if status not in _VALID_STATUSES:
            raise ValueError(f"status must be one of {sorted(_VALID_STATUSES)}, got {status!r}")
        status_clause = "AND status = :status"
        params["status"] = status
    result = await db.execute(
        text(f"""
            SELECT id::text, session_id, title, objective, status,
                   created_at, updated_at
            FROM investigations
            WHERE created_by = :uid
              {status_clause}
            ORDER BY updated_at DESC, id DESC
            LIMIT :lim
        """),
        params,
    )
    return [dict(r._mapping) for r in result]


async def update_investigation_status(
    db: AsyncSession,
    investigation_id: str,
    user_id: str,
    status: str,
) -> bool:
    """Move an investigation to active / paused / complete.

    Owner-scoped via `created_by = :uid`. Returns True if the row was
    updated, False if it didn't exist or wasn't owned by the caller.
    """
    if status not in _VALID_STATUSES:
        raise ValueError(f"status must be one of {sorted(_VALID_STATUSES)}, got {status!r}")
    async with db.begin():
        result = await db.execute(
            text("""
                UPDATE investigations
                   SET status = :status,
                       updated_at = NOW()
                 WHERE id = CAST(:iid AS uuid)
                   AND created_by = :uid
            """),
            {"iid": investigation_id, "uid": user_id, "status": status},
        )
        return result.rowcount > 0  # type: ignore[attr-defined]


async def touch_investigation(
    db: AsyncSession,
    investigation_id: str,
) -> None:
    """Bump `updated_at` so list_investigations re-orders. No ownership check —
    intended to be called from within a transaction that already validated
    ownership (via a write to a child table that was owner-scoped)."""
    await db.execute(
        text("""
            UPDATE investigations
               SET updated_at = NOW()
             WHERE id = CAST(:iid AS uuid)
        """),
        {"iid": investigation_id},
    )
