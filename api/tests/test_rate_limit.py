"""Rate-limit boundary + key-sanitization tests (Tier D1.2 from BACKLOG.md)."""
from __future__ import annotations

import uuid

import pytest

from api.db.queries.rate_limit import make_key, pg_rate_limit


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
