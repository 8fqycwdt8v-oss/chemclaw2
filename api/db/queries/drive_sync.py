"""Drive-sync state queries — per-drive Microsoft Graph delta cursor.

The SharePoint/OneDrive sync worker (added in a later slice) reads the stored
delta token before each run and records the new token + run status after. Only
this module touches SQLAlchemy primitives for the `drive_sync_state` table
(CLAUDE.md queries-layer rule).
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def get_sync_state(db: AsyncSession, drive_id: str) -> dict[str, Any] | None:
    """Return the stored sync state for a drive, or None if never synced.

    Read-only — does not commit.
    """
    result = await db.execute(
        text(
            """
            SELECT drive_id, delta_token, last_synced_at, last_status, last_error
            FROM drive_sync_state
            WHERE drive_id = :drive_id
            """
        ),
        {"drive_id": drive_id},
    )
    row = result.mappings().one_or_none()
    return dict(row) if row else None


async def save_sync_state(
    db: AsyncSession,
    drive_id: str,
    *,
    status: str,
    delta_token: str | None = None,
    error: str | None = None,
) -> None:
    """Record the outcome of a sync run.

    Always advances `last_synced_at` to NOW() so the cadence gate sees the
    attempt. `delta_token` is only written when supplied (success) — on an
    error pass `delta_token=None` and the previous cursor is preserved via
    COALESCE so the next run retries from the same point.

    Wraps the upsert in `async with db.begin()` per CLAUDE.md transaction rule.
    """
    async with db.begin():
        await db.execute(
            text(
                """
                INSERT INTO drive_sync_state
                    (drive_id, delta_token, last_synced_at, last_status, last_error, updated_at)
                VALUES (:drive_id, :delta_token, NOW(), :status, :error, NOW())
                ON CONFLICT (drive_id) DO UPDATE SET
                    delta_token    = COALESCE(EXCLUDED.delta_token, drive_sync_state.delta_token),
                    last_synced_at = EXCLUDED.last_synced_at,
                    last_status    = EXCLUDED.last_status,
                    last_error     = EXCLUDED.last_error,
                    updated_at     = NOW()
                """
            ),
            {
                "drive_id": drive_id,
                "delta_token": delta_token,
                "status": status,
                "error": error,
            },
        )
