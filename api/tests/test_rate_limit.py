"""Rate-limit boundary + key-sanitization tests (Tier D1.2 from BACKLOG.md)."""
from __future__ import annotations

import uuid

import pytest

from api.db.queries.rate_limit import make_key, pg_rate_limit, sweep_rate_limit_rows


def test_make_key_strips_colons():
    # Lossless hex-escape: every unsafe byte becomes "_xx_". Safe chars
    # (letters, digits, '-') pass through. '_' is escaped too so the
    # marker is unambiguous.
    assert make_key("wiki", "alice:bob") == "wiki:alice_3a_bob"
    assert make_key("wiki", "org abc") == "wiki:org_20_abc"
    assert make_key("wiki", "u/1") == "wiki:u_2f_1"
    assert make_key("wiki", "user-1_abc") == "wiki:user-1_5f_abc"
    # Hyphens and alphanumerics survive untouched.
    assert make_key("wiki", "user-1-abc") == "wiki:user-1-abc"


def test_make_key_handles_none():
    assert make_key("wiki", None) == "wiki:anon"
    assert make_key("wiki", "") == "wiki:anon"


def test_make_key_prevents_aliasing():
    """Two different identifiers must produce different keys, even when they
    'collide' across the bucket separator before sanitization."""
    a = make_key("foo", "alice")
    b = make_key("foo", "x:alice")
    assert a != b
    assert a == "foo:alice"
    assert b == "foo:x_3a_alice"


@pytest.mark.asyncio
async def test_pg_rate_limit_boundary(db):
    key = f"test-rl-{uuid.uuid4().hex[:8]}"
    # First 3 requests within the 3-request window: not limited.
    for i in range(3):
        r = await pg_rate_limit(db, key, max_requests=3, window_ms=60_000)
        assert r["limited"] is False, f"call {i+1} should be allowed"
    # 4th request: over the limit.
    r = await pg_rate_limit(db, key, max_requests=3, window_ms=60_000)
    assert r["limited"] is True


@pytest.mark.asyncio
async def test_pg_rate_limit_exactly_at_limit(db):
    """Boundary: count == max_requests is NOT limited (the >= check used the
    wrong comparator in earlier TS code; pin the Python semantics here).
    """
    key = f"test-rl-eq-{uuid.uuid4().hex[:8]}"
    r1 = await pg_rate_limit(db, key, max_requests=1, window_ms=60_000)
    assert r1["limited"] is False
    r2 = await pg_rate_limit(db, key, max_requests=1, window_ms=60_000)
    assert r2["limited"] is True


# ── sweep_rate_limit_rows ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sweep_rate_limit_rows_deletes_old(session_factory):
    """Rows with window_start older than max_age_ms are deleted; fresh
    rows survive. The cleanup pass is what keeps the rate_limits table
    from growing unboundedly under the fixed-window upsert pattern."""
    import time

    from sqlalchemy import text

    old_key = f"sweep-old-{uuid.uuid4().hex[:8]}"
    fresh_key = f"sweep-fresh-{uuid.uuid4().hex[:8]}"
    now_ms = int(time.time() * 1000)
    old_window = now_ms - 10_000_000  # well past the 2-hour cap
    fresh_window = now_ms - 60_000  # 1 min old — keep

    async with session_factory() as db:
        async with db.begin():
            await db.execute(
                text(
                    "INSERT INTO rate_limits (key, window_start, count) "
                    "VALUES (:k, :w, 1)"
                ),
                {"k": old_key, "w": old_window},
            )
            await db.execute(
                text(
                    "INSERT INTO rate_limits (key, window_start, count) "
                    "VALUES (:k, :w, 1)"
                ),
                {"k": fresh_key, "w": fresh_window},
            )

    async with session_factory() as db:
        deleted = await sweep_rate_limit_rows(db, max_age_ms=7_200_000)
    assert deleted >= 1

    async with session_factory() as db:
        old_row = await db.execute(
            text("SELECT 1 FROM rate_limits WHERE key = :k"), {"k": old_key}
        )
        fresh_row = await db.execute(
            text("SELECT 1 FROM rate_limits WHERE key = :k"), {"k": fresh_key}
        )
    assert old_row.one_or_none() is None
    assert fresh_row.one_or_none() is not None


@pytest.mark.asyncio
async def test_sweep_rate_limit_rows_noop_when_empty(session_factory):
    """The sweep over a table with no expired rows returns 0 without erroring."""
    async with session_factory() as db:
        deleted = await sweep_rate_limit_rows(db, max_age_ms=7_200_000)
    assert deleted >= 0  # may delete leftovers from other tests, but doesn't error
