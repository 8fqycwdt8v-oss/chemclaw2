"""Rate limiter — Python port of packages/db/src/queries/rate-limit.ts.

Fixed-window, Postgres-backed. Fails CLOSED on DB error.
"""
from __future__ import annotations

import logging
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def pg_rate_limit(
    db: AsyncSession,
    key: str,
    max_requests: int,
    window_ms: int,
) -> dict[str, bool]:
    window_start = (int(time.time() * 1000) // window_ms) * window_ms
    try:
        result = await db.execute(
            text("""
                INSERT INTO rate_limits (key, window_start, count)
                VALUES (:key, :window_start, 1)
                ON CONFLICT (key, window_start)
                DO UPDATE SET count = rate_limits.count + 1
                RETURNING count
            """),
            {"key": key, "window_start": window_start},
        )
        row = result.one_or_none()
        if row is None:
            logger.error("rate_limit_no_row_returned", extra={"key": key})
            return {"limited": True}
        await db.commit()
        return {"limited": row.count > max_requests}
    except Exception:
        logger.exception("rate_limit_db_fail_closed", extra={"key": key})
        return {"limited": True}
