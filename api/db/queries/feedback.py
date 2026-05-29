from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.queries._helpers import rows_to_dicts

logger = logging.getLogger(__name__)


async def record_feedback(
    db: AsyncSession,
    session_id: str,
    turn_index: int,
    score: int,
    user_id: str,
    reason: str | None = None,
) -> str:
    """Insert or update feedback for a conversation turn. Returns the feedback id."""
    async with db.begin():
        result = await db.execute(
            text("""
                INSERT INTO agent_feedback (session_id, turn_index, score, reason, user_id)
                VALUES (:session_id, :turn_index, :score, :reason, :user_id)
                ON CONFLICT (session_id, turn_index, user_id) DO UPDATE SET
                    score  = EXCLUDED.score,
                    reason = EXCLUDED.reason
                RETURNING id::text
            """),
            {
                "session_id": session_id,
                "turn_index": turn_index,
                "score": score,
                "reason": reason,
                "user_id": user_id,
            },
        )
        return result.scalar_one()


async def list_session_feedback(
    db: AsyncSession,
    session_id: str,
    user_id: str,
) -> list[dict[str, Any]]:
    """List all feedback for a session, scoped to the requesting user."""
    result = await db.execute(
        text("""
            SELECT id::text, turn_index, score, reason, created_at
            FROM agent_feedback
            WHERE session_id = :session_id
              AND user_id = :user_id
            ORDER BY turn_index
        """),
        {"session_id": session_id, "user_id": user_id},
    )
    return rows_to_dicts(result)
