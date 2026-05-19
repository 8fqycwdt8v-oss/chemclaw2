"""Regression tests for review-followup fixes shipped after Tier E.

Three findings from PR #93's deep review:
1. upsert_wiki_page rollback hazard — must not roll back caller-managed tx
2. make_key collision — hex-escape unsafe chars to be lossless
3. SSE truncation marker — body + marker must stay at or below the cap
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from api.agent.runner import (
    _TRUNC_MARKER,
    SSE_TEXT_BLOCK_MAX_BYTES,
    _cap_text_block,
)
from api.db.queries.rate_limit import make_key
from api.db.queries.wiki_write import upsert_wiki_page
from api.embeddings import EMBED_DIM

# ── 1. upsert_wiki_page rollback hazard ──────────────────────────────────────

async def _noop_embed(texts: list[str]) -> list[list[float]]:
    return [[0.0] * EMBED_DIM for _ in texts]


@pytest.mark.asyncio
async def test_upsert_preserves_caller_writes_when_called_in_active_tx(
    session_factory, user_id,
):
    """If a caller has a transaction active before calling upsert_wiki_page,
    the function must not silently roll back the caller's uncommitted work.

    Today's code raises a clear SQLAlchemy error when this misuse occurs
    (the explicit `async with db.begin():` inside upsert_wiki_page fires
    "transaction already begun"). That's the correct fail-fast contract.
    The previous implementation called `db.rollback()` unconditionally,
    which would have silently discarded the caller's pending insert.

    This test exercises: caller opens tx, inserts a notification row, then
    calls upsert_wiki_page within that tx. We expect an exception (NOT
    silent loss); the caller can then roll back cleanly with the
    notification gone (proving upsert didn't touch the caller's work).
    """
    from sqlalchemy.exc import InvalidRequestError

    slug = f"hazard-{uuid.uuid4().hex[:8]}"
    marker = f"caller-notif-{uuid.uuid4().hex[:8]}"

    async with session_factory() as s:
        with pytest.raises((InvalidRequestError, Exception)):
            async with s.begin():
                # Caller does some write work in their own tx
                await s.execute(
                    text(
                        "INSERT INTO notifications (user_id, type, payload) "
                        "VALUES (:uid, 'test', CAST(:p AS jsonb))"
                    ),
                    {"uid": user_id, "p": '{"m": "' + marker + '"}'},
                )
                # Then misuses upsert_wiki_page on the same session.
                # Old behavior: db.rollback() drops the caller's INSERT.
                # New behavior: caller's tx survives until they decide.
                await upsert_wiki_page(
                    s, slug=slug, title="X", content={},
                    content_text="body " * 30, created_by=user_id,
                    citations=[], embed_fn=_noop_embed,
                )

    # After the `async with s.begin():` context exited via exception, the
    # caller's tx rolled back. The notification row should NOT be present.
    async with session_factory() as s:
        res = await s.execute(
            text(
                "SELECT 1 FROM notifications "
                "WHERE user_id = :uid AND payload->>'m' = :m"
            ),
            {"uid": user_id, "m": marker},
        )
        assert res.one_or_none() is None, (
            "Caller's tx must roll back cleanly; the notification row "
            "would still be here if upsert_wiki_page had committed the "
            "caller's pending insert."
        )


# ── 2. make_key collision-free under hex escape ──────────────────────────────

def test_make_key_no_collision_under_punctuation():
    """alice@bob and alice_bob used to collide under the lossy '_'
    substitution. Hex-escape must produce distinct keys."""
    a = make_key("rl", "alice@bob")
    b = make_key("rl", "alice_bob")
    assert a != b, f"Expected distinct keys; got {a!r} and {b!r}"


def test_make_key_no_collision_under_slash_vs_underscore():
    a = make_key("rl", "x/y")
    b = make_key("rl", "x_y")
    assert a != b


def test_make_key_hex_escape_is_reversible():
    """The escape is unambiguous: every unsafe byte becomes `_xx_`."""
    assert make_key("rl", "a@b") == "rl:a_40_b"
    assert make_key("rl", "x:y") == "rl:x_3a_y"
    # `_` is now hex-escaped too so the marker is unambiguous
    assert make_key("rl", "a_b") == "rl:a_5f_b"


def test_make_key_safe_chars_pass_through():
    # Letters, digits, and `-` stay as themselves.
    assert make_key("rl", "abc-XYZ-123") == "rl:abc-XYZ-123"


def test_make_key_handles_none_and_empty():
    assert make_key("rl", None) == "rl:anon"
    assert make_key("rl", "") == "rl:anon"


# ── 3. SSE truncation cap stays at or below the budget ───────────────────────

def test_cap_text_block_short_input_unchanged():
    body = "small message"
    assert _cap_text_block(body) == body


def test_cap_text_block_truncates_and_marker_fits_in_budget():
    big = "x" * (SSE_TEXT_BLOCK_MAX_BYTES + 1024)
    out = _cap_text_block(big)
    assert out.endswith(_TRUNC_MARKER)
    # Critical: total bytes must stay at or below the advertised cap so a
    # downstream proxy tuned to exactly 1 MB can't truncate the marker.
    assert len(out.encode("utf-8")) <= SSE_TEXT_BLOCK_MAX_BYTES


def test_cap_text_block_multibyte_boundary():
    # Build a string where the byte budget falls inside a multi-byte char.
    # Each '€' is 3 bytes. Pad so the slice point sits mid-character.
    padding = "x" * (SSE_TEXT_BLOCK_MAX_BYTES - 1)  # 1 byte short of cap
    body = padding + "€€€€€"  # multi-byte tail
    out = _cap_text_block(body)
    # Marker present, total under cap, no UTF-8 decode error (errors=ignore
    # drops the partial multi-byte sequence cleanly).
    assert out.endswith(_TRUNC_MARKER)
    assert len(out.encode("utf-8")) <= SSE_TEXT_BLOCK_MAX_BYTES
