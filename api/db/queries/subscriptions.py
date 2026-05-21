"""Wiki subscription queries — per-user subscriptions to wiki pages.

Callers that need atomicity must use `async with db.begin()`. Functions that
mutate state manage their own transaction via `async with db.begin()`.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def subscribe(db: AsyncSession, user_id: str, page_id: str) -> None:
    """Subscribe user_id to page_id. Idempotent — duplicate inserts are silently ignored."""
    async with db.begin():
        await db.execute(
            text("""
                INSERT INTO wiki_subscriptions (user_id, page_id)
                VALUES (:uid, CAST(:pid AS uuid))
                ON CONFLICT DO NOTHING
            """),
            {"uid": user_id, "pid": page_id},
        )


async def unsubscribe(db: AsyncSession, user_id: str, page_id: str) -> bool:
    """Remove subscription for (user_id, page_id). Returns True if a row was deleted."""
    async with db.begin():
        result = await db.execute(
            text("""
                DELETE FROM wiki_subscriptions
                WHERE user_id = :uid AND page_id = CAST(:pid AS uuid)
                RETURNING page_id
            """),
            {"uid": user_id, "pid": page_id},
        )
        return result.one_or_none() is not None


async def list_subscriptions(db: AsyncSession, user_id: str) -> list[dict[str, Any]]:
    """Return all subscriptions for user_id joined with current page metadata."""
    result = await db.execute(
        text("""
            SELECT ws.page_id::text,
                   ws.last_seen_version,
                   ws.created_at,
                   wp.slug,
                   wp.title,
                   wp.version AS current_version
            FROM wiki_subscriptions ws
            JOIN wiki_pages wp ON wp.id = ws.page_id
            WHERE ws.user_id = :uid
            ORDER BY ws.created_at DESC
        """),
        {"uid": user_id},
    )
    return [dict(r._mapping) for r in result]


async def mark_seen(
    db: AsyncSession,
    user_id: str,
    page_id: str,
    version: int,
) -> None:
    """Advance last_seen_version to version for (user_id, page_id).

    Only updates when the stored value is strictly less than version so that
    out-of-order calls cannot regress the cursor.
    """
    async with db.begin():
        await db.execute(
            text("""
                UPDATE wiki_subscriptions
                SET last_seen_version = :v
                WHERE user_id = :uid
                  AND page_id = CAST(:pid AS uuid)
                  AND last_seen_version < :v
            """),
            {"uid": user_id, "pid": page_id, "v": version},
        )
