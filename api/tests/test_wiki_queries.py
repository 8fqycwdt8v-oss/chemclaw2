"""Tier D1.3, D1.4 — wiki query unit tests against the real DB.

Covers:
- get_wiki_page_at: as_of after last edit returns the current row (the bug
  resolved in production-readiness phase 1 — pin that behavior).
- upsert_wiki_page: content-hash skip (no re-embed when text unchanged).
- list / get / FTS round-trip.
"""
from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from api.db.queries.wiki_read import (
    get_wiki_page,
    get_wiki_page_at,
    search_wiki_by_fts,
)
from api.db.queries.wiki_write import chunk_text, upsert_wiki_page
from api.embeddings import EMBED_DIM


async def _noop_embed(texts: list[str]) -> list[list[float]]:
    return [[0.0] * EMBED_DIM for _ in texts]


# ── upsert / get round-trip ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_upsert_then_get(db, user_id):
    slug = f"rt-{uuid.uuid4().hex[:8]}"
    await upsert_wiki_page(
        db,
        slug=slug,
        title="Round-Trip",
        content={"type": "doc", "content": []},
        content_text="A B C D E F G H I J K L M N O P Q R S T U V " * 5,
        created_by=user_id,
        citations=[],
        embed_fn=_noop_embed,
    )
    page = await get_wiki_page(db, slug)
    assert page is not None
    assert page["slug"] == slug
    assert page["title"] == "Round-Trip"
    assert page["created_by"] == user_id
    assert page["version"] == 1


@pytest.mark.asyncio
async def test_upsert_increments_version_on_change(db, user_id):
    slug = f"vbump-{uuid.uuid4().hex[:8]}"
    content_a = "First version content " * 5
    content_b = "Second version content " * 5

    await upsert_wiki_page(
        db, slug=slug, title="V1", content={}, content_text=content_a,
        created_by=user_id, citations=[], embed_fn=_noop_embed,
    )
    p1 = await get_wiki_page(db, slug)
    assert p1["version"] == 1

    await upsert_wiki_page(
        db, slug=slug, title="V2", content={}, content_text=content_b,
        created_by=user_id, citations=[], embed_fn=_noop_embed,
    )
    p2 = await get_wiki_page(db, slug)
    assert p2["version"] == 2


@pytest.mark.asyncio
async def test_upsert_skips_embed_when_content_unchanged(db, user_id):
    """Hash-skip: embed_fn must NOT be called when content_text is unchanged."""
    slug = f"hashskip-{uuid.uuid4().hex[:8]}"
    body = "Stable content for the hash-skip path " * 5

    call_counter = {"n": 0}

    async def counting_embed(texts: list[str]) -> list[list[float]]:
        call_counter["n"] += 1
        return [[0.0] * EMBED_DIM for _ in texts]

    await upsert_wiki_page(
        db, slug=slug, title="Hash", content={}, content_text=body,
        created_by=user_id, citations=[], embed_fn=counting_embed,
    )
    first = call_counter["n"]
    assert first >= 1  # first insert did embed

    # Re-upsert with identical content_text → must NOT embed again.
    await upsert_wiki_page(
        db, slug=slug, title="Hash again", content={}, content_text=body,
        created_by=user_id, citations=[], embed_fn=counting_embed,
    )
    assert call_counter["n"] == first, "embed_fn should not be called when content_text is unchanged"


# ── chunker ──────────────────────────────────────────────────────────────────

def test_chunk_text_empty():
    assert chunk_text("") == []
    assert chunk_text("   ") == []


def test_chunk_text_paragraph_under_limit():
    body = "A short paragraph that comfortably fits in one chunk of 400 chars."
    chunks = chunk_text(body, chunk_size=400)
    # Below the _MIN_CHUNK_LENGTH=50 filter, it's still kept because it's >= 50.
    assert len(chunks) == 1
    assert chunks[0].startswith("A short paragraph")


def test_chunk_text_splits_on_paragraphs():
    body = "First paragraph " * 30 + "\n\n" + "Second paragraph " * 30
    chunks = chunk_text(body, chunk_size=200, overlap=40)
    assert len(chunks) >= 2


# ── get_wiki_page_at: temporal correctness ───────────────────────────────────

@pytest.mark.asyncio
async def test_get_wiki_page_at_returns_current_when_asof_after_last_edit(
    db, user_id,
):
    """Bug fix from production-readiness phase 1: when as_of is AFTER the
    last edit, the current row must be returned (not the second-to-last
    revision). Pin that behavior."""
    slug = f"asof-{uuid.uuid4().hex[:8]}"
    await upsert_wiki_page(
        db, slug=slug, title="T1", content={}, content_text="content v1 " * 10,
        created_by=user_id, citations=[], embed_fn=_noop_embed,
    )
    # Small sleep so as_of can be after the page's updated_at without races.
    await asyncio.sleep(0.05)
    future = datetime.now(timezone.utc) + timedelta(minutes=5)

    page = await get_wiki_page_at(db, slug, future)
    assert page is not None
    assert page["slug"] == slug
    assert page["title"] == "T1"


@pytest.mark.asyncio
async def test_get_wiki_page_at_returns_none_when_asof_before_creation(
    db, user_id,
):
    slug = f"past-{uuid.uuid4().hex[:8]}"
    await upsert_wiki_page(
        db, slug=slug, title="T", content={}, content_text="x " * 30,
        created_by=user_id, citations=[], embed_fn=_noop_embed,
    )
    past = datetime.now(timezone.utc) - timedelta(days=365)
    assert await get_wiki_page_at(db, slug, past) is None


# ── FTS ──────────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_fts_finds_inserted_page(db, user_id):
    needle = f"unique-keyword-{uuid.uuid4().hex[:8]}"
    slug = f"fts-{uuid.uuid4().hex[:8]}"
    await upsert_wiki_page(
        db, slug=slug, title="FTS", content={},
        content_text=f"This wiki entry contains the {needle} marker " * 5,
        created_by=user_id, citations=[], embed_fn=_noop_embed,
    )
    hits = await search_wiki_by_fts(db, needle, limit=5)
    assert any(h["slug"] == slug for h in hits), f"Expected slug {slug} in FTS results"
