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

# Anything that isn't a safe identifier character gets hex-escaped as `_xx_`
# (where xx is the byte's hex code) so a user id like "org_abc:user_xyz"
# can't alias buckets across the ':' bucket separator. Hex escape is
# lossless — two distinct identifiers cannot collide on the sanitized key,
# unlike a simple `replace-with-_` which mapped "alice@bob" and "alice_bob"
# to the same bucket.
# `_` itself is escaped (→ `_5f_`) so the hex-marker is unambiguous.
_RL_UNSAFE = re.compile(r"[^A-Za-z0-9-]")


def _escape_byte(m: "re.Match[str]") -> str:
    # Hex-encode each byte of the matched code-point so the result stays
    # 7-bit ASCII regardless of input encoding.
    return "".join(f"_{b:02x}_" for b in m.group(0).encode("utf-8"))


def make_key(bucket: str, identifier: str | None) -> str:
    """Compose a rate-limit bucket name.

    The ':' character is reserved as the bucket/identifier separator. Clerk
    user ids and other external identifiers are not guaranteed colon-free,
    and lossy sanitization (replace unsafe chars with '_') would create
    cross-user bucket collisions. Hex-escape the identifier instead.
    """
    safe = _RL_UNSAFE.sub(_escape_byte, identifier) if identifier else "anon"
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
                RETURNING count AS cnt
            """),
            {"key": key, "window_start": window_start},
        )
        row = result.one_or_none()
        if row is None:
            logger.error("rate_limit_no_row_returned", extra={"key": key})
            return {"limited": True}
        await db.commit()
        # Alias the returned column to `cnt` because `row.count` is otherwise
        # the SQLAlchemy Row built-in count() method, not the column.
        return {"limited": row.cnt > max_requests}
    except Exception:
        logger.exception("rate_limit_db_fail_closed", extra={"key": key})
        return {"limited": True}
