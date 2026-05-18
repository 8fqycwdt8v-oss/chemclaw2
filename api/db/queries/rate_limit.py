"""Rate limiter — Python port of packages/db/src/queries/rate-limit.ts.

Fixed-window, Postgres-backed.

Failure mode: FAIL CLOSED (blocks requests on DB error).
This differs from the TypeScript port which fails open (allows requests on DB
error to prevent a DB outage from taking down the API). The Python backend
chooses fail-closed because security (preventing abuse) takes precedence over
availability — operators should resolve DB issues rather than silently
bypassing rate limits. The error is always logged so a DB blip is visible in
metrics. If you need fail-open behaviour, change the except clause to
`return {"limited": False}` and document the reason at the call site.
"""
from __future__ import annotations

import logging
import re
import time

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

# Anything that isn't a safe identifier character gets replaced with '_' so a
# user id like "org_abc:user_xyz" can't alias buckets across the ':' bucket
# separator.
_RL_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


def make_key(bucket: str, identifier: str | None) -> str:
    """Compose a rate-limit bucket name.

    The ':' character is reserved as the bucket/identifier separator. Clerk
    user ids and other external identifiers are not guaranteed colon-free, so
    sanitize identifier before composing.
    """
    safe = _RL_UNSAFE.sub("_", identifier) if identifier else "anon"
    return f"{bucket}:{safe}"


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
