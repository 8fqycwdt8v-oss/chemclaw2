"""Todos queries — agent_todos table CRUD."""
from __future__ import annotations

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def list_todos(db: AsyncSession, session_id: str, user_id: str) -> list[dict]:
    """Return all todos for (session_id, user_id) ordered by position."""
    result = await db.execute(
        text("""
            SELECT id, text, status, position, created_at, updated_at
            FROM agent_todos
            WHERE session_id = :sid AND user_id = :uid
            ORDER BY position
        """),
        {"sid": session_id, "uid": user_id},
    )
    rows = result.mappings().fetchall()
    return [dict(r) for r in rows]


async def upsert_todos(
    db: AsyncSession,
    session_id: str,
    user_id: str,
    todos: list[dict],
) -> None:
    """Replace all todos for (session_id, user_id) atomically.

    Deletes existing rows, then batch-inserts the supplied todos.
    Each todo dict must have keys: text, status, position.
    """
    async with db.begin():
        await db.execute(
            text("DELETE FROM agent_todos WHERE session_id = :sid AND user_id = :uid"),
            {"sid": session_id, "uid": user_id},
        )
        if todos:
            await db.execute(
                text("""
                    INSERT INTO agent_todos (session_id, user_id, text, status, position)
                    SELECT
                        :session_id,
                        :user_id,
                        v.text,
                        v.status,
                        v.position
                    FROM (
                        SELECT
                            unnest(CAST(:texts AS text[]))  AS text,
                            unnest(CAST(:statuses AS text[])) AS status,
                            unnest(CAST(:positions AS int[])) AS position
                    ) v
                """),
                {
                    "session_id": session_id,
                    "user_id": user_id,
                    "texts": [t["text"] for t in todos],
                    "statuses": [t["status"] for t in todos],
                    "positions": [t["position"] for t in todos],
                },
            )


async def mark_todo_done(db: AsyncSession, todo_id: str, user_id: str) -> bool:
    """Set status='done' on a single todo.

    Returns True if the row existed and was updated, False otherwise.
    """
    async with db.begin():
        result = await db.execute(
            text("""
                UPDATE agent_todos
                SET status = 'done', updated_at = NOW()
                WHERE id = CAST(:id AS uuid) AND user_id = :uid
            """),
            {"id": todo_id, "uid": user_id},
        )
        # SQLAlchemy 2.0 annotates Result.rowcount only on CursorResult.
        # For DML it is always populated; mypy can't narrow the runtime type.
        return result.rowcount > 0
