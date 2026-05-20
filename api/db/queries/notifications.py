"""Notification queries."""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def create_notification(
    db: AsyncSession, user_id: str, type: str, payload: dict[str, Any]
) -> str:
    """Insert a notification. Returns id. Does NOT commit — caller owns transaction."""
    result = await db.execute(
        text(
            """
            INSERT INTO notifications (user_id, type, payload)
            VALUES (:uid, :type, CAST(:payload AS jsonb))
            RETURNING id::text
            """
        ),
        {"uid": user_id, "type": type, "payload": json.dumps(payload)},
    )
    row = result.one()
    return row.id


async def list_notifications(
    db: AsyncSession,
    user_id: str,
    unread_only: bool = True,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List notifications for a user."""
    filter_clause = "AND read = FALSE" if unread_only else ""
    result = await db.execute(
        text(
            f"""
            SELECT id::text, user_id, type, payload, read, created_at
            FROM notifications
            WHERE user_id = :uid
            {filter_clause}
            ORDER BY created_at DESC
            LIMIT :lim
            """
        ),
        {"uid": user_id, "lim": limit},
    )
    rows = result.mappings().all()
    return [dict(r) for r in rows]


async def count_unread(db: AsyncSession, user_id: str) -> int:
    """Count unread notifications for a user."""
    result = await db.execute(
        text(
            """
            SELECT count(*) AS cnt
            FROM notifications
            WHERE user_id = :uid AND read = FALSE
            """
        ),
        {"uid": user_id},
    )
    row = result.one()
    return int(row.cnt)


async def mark_read(
    db: AsyncSession, user_id: str, notification_ids: list[str]
) -> int:
    """Mark specific notifications as read. Returns count updated."""
    async with db.begin():
        result = await db.execute(
            text(
                """
                UPDATE notifications
                SET read = TRUE
                WHERE user_id = :uid
                  AND id = ANY(CAST(:ids AS uuid[]))
                  AND read = FALSE
                """
            ),
            {"uid": user_id, "ids": notification_ids},
        )
        # SQLAlchemy 2.0 annotates rowcount only on CursorResult; DML
        # always returns one but mypy can't narrow the runtime type.
        return result.rowcount  # type: ignore[attr-defined]


async def mark_all_read(db: AsyncSession, user_id: str) -> int:
    """Mark all notifications as read. Returns count updated."""
    async with db.begin():
        result = await db.execute(
            text(
                """
                UPDATE notifications
                SET read = TRUE
                WHERE user_id = :uid AND read = FALSE
                """
            ),
            {"uid": user_id},
        )
        return result.rowcount  # type: ignore[attr-defined]
