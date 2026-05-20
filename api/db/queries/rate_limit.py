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

Routes should prefer the `rate_limit` FastAPI dependency in
`api.db.queries.rate_limit` (factory at the bottom of this module) over
inline `pg_rate_limit` calls — keeps the 4-line guard out of every handler.
"""
from __future__ import annotations

import logging
import re
import time
from fastapi import Depends, HTTPException
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


async def sweep_rate_limit_rows(
    db: AsyncSession, max_age_ms: int = 7_200_000
) -> int:
    """Delete rate_limit rows whose window_start is older than `max_age_ms`.

    The pg_rate_limit upsert never expires rows; over time the table grows
    unboundedly and the GIST/B-tree on (key, window_start) gets slower.
    Two hours by default — comfortably wider than any rate-limit window
    actually in use (the longest is 60 s today). Returns the number of
    rows deleted so the worker can log it.

    Wraps its own transaction. Safe to call from any worker context.
    """
    cutoff_ms = int(time.time() * 1000) - max_age_ms
    async with db.begin():
        result = await db.execute(
            text("DELETE FROM rate_limits WHERE window_start < :cutoff"),
            {"cutoff": cutoff_ms},
        )
        # CursorResult.rowcount is populated for DML; mypy doesn't narrow it.
        return result.rowcount


# ── FastAPI dependency factory ────────────────────────────────────────────────


def rate_limit(
    bucket: str,
    max_requests: int,
    window_ms: int = 60_000,
    *,
    optional_user: bool = False,
):
    """Build a FastAPI dependency that enforces a per-user rate limit.

    Usage:
        @router.get("/api/wiki", dependencies=[Depends(rate_limit("wiki-list", 60))])

    The bucket name is hex-escaped against the caller's `user_id`; admin
    routes that already depend on `get_admin_user` reuse the same caller
    identity (FastAPI deduplicates `get_current_user` across the request).

    Set `optional_user=True` for endpoints that allow anonymous reads;
    anonymous callers share one "anon" bucket (matches how `make_key`
    handles a None identifier).

    Fail-closed: a DB error in `pg_rate_limit` returns `{"limited": True}`
    and this dep raises 429 — same posture as inline call sites.
    """
    # Import inside the factory to avoid an import cycle.
    from api.auth import get_current_user, get_optional_user
    from api.db.connection import get_db

    # IMPORTANT: this module has `from __future__ import annotations`, so the
    # parameter annotations are evaluated as strings. The
    # `Annotated[AsyncSession, Depends(get_db)]` form turns into a ForwardRef
    # that Pydantic cannot resolve at OpenAPI generation time — FastAPI then
    # falls back to treating `db` as a query parameter and the route 422s.
    # Use the older `default=Depends(...)` syntax (with no Annotated wrapper)
    # which FastAPI introspects directly via `inspect.signature`, sidestepping
    # the Pydantic ForwardRef path entirely.
    if optional_user:
        async def _dep_optional(
            db: AsyncSession = Depends(get_db),
            user_id: str | None = Depends(get_optional_user),
        ) -> None:
            result = await pg_rate_limit(db, make_key(bucket, user_id), max_requests, window_ms)
            if result["limited"]:
                logger.warning("rate_limit_denied bucket=%s user=%s", bucket, user_id or "anon")
                raise HTTPException(status_code=429, detail="Too many requests")

        _dep_optional.__name__ = f"rate_limit_{bucket.replace('-', '_')}"
        return _dep_optional

    async def _dep(
        db: AsyncSession = Depends(get_db),
        user_id: str = Depends(get_current_user),
    ) -> None:
        result = await pg_rate_limit(db, make_key(bucket, user_id), max_requests, window_ms)
        if result["limited"]:
            logger.warning("rate_limit_denied bucket=%s user=%s", bucket, user_id)
            raise HTTPException(status_code=429, detail="Too many requests")

    _dep.__name__ = f"rate_limit_{bucket.replace('-', '_')}"
    return _dep
