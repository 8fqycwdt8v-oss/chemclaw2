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

from api.db.queries._helpers import clamp_limit, row_to_dict, rows_to_dicts, validate_enum

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
    return row_to_dict(row)


async def list_investigations(
    db: AsyncSession,
    user_id: str,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List investigations owned by user_id, newest-updated first."""
    safe_limit = clamp_limit(limit, 200)
    params: dict[str, Any] = {"uid": user_id, "lim": safe_limit}
    status_clause = ""
    if status is not None:
        validate_enum(status, _VALID_STATUSES, "status")
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
    return rows_to_dicts(result)


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
    validate_enum(status, _VALID_STATUSES, "status")
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


async def get_or_create_corpus_investigation(
    db: AsyncSession,
    title: str,
    objective: str,
    created_by: str,
) -> str:
    """Return the id of the investigation owned by `created_by` with this exact
    `title`, creating it if none exists.

    Used as the anchor for document-derived world-model entries (the "corpus"
    investigation for a drive or for a user's uploads). There's no unique
    constraint on (created_by, title); a concurrent first-call race could in
    principle create two, but the drive-sync worker holds a per-drive advisory
    lock and the upload path is low-frequency, so the benign duplicate is
    acceptable rather than adding an index. Owner-scoped on `created_by`.

    The SELECT and the fallback INSERT share one `db.begin()` block: a
    SQLAlchemy 2.0 async SELECT auto-begins a transaction, so delegating to
    `create_investigation` (which opens its own `db.begin()`) would raise "a
    transaction is already begun" — the read-then-begin antipattern noted in
    BACKLOG.md. Inlining the insert keeps it to a single transaction.
    """
    async with db.begin():
        existing = await db.execute(
            text("""
                SELECT id::text
                FROM investigations
                WHERE created_by = :uid AND title = :title
                ORDER BY created_at
                LIMIT 1
            """),
            {"uid": created_by, "title": title},
        )
        row = existing.first()
        if row is not None:
            return row[0]
        created = await db.execute(
            text("""
                INSERT INTO investigations (session_id, title, objective, created_by)
                VALUES (NULL, :title, :obj, :uid)
                RETURNING id::text
            """),
            {"title": title, "obj": objective, "uid": created_by},
        )
        return created.scalar_one()

