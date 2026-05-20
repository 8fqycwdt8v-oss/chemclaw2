"""Curator inbox aggregation tests.

DB-backed: seeds wiki pages with `needs_review=true`, campaign steps in
`pending_approval`, and unresolved contradictions for two users, then
asserts the inbox returns the right buckets for each owner. Lock in
the owner-scoping for the wiki + step legs (contradictions are
collaborative by design).

Endpoint-level tests are sync (using the TestClient `client` fixture)
because pytest-asyncio + TestClient combine awkwardly — TestClient
spins up its own event loop, which deadlocks inside an async test.
DB seeding for those tests runs via `asyncio.run()` against the same
session factory the conftest exposes.
"""
from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import text

from api.db.queries.campaigns import add_campaign_step
from api.db.queries.contradictions import create_contradiction
from api.db.queries.wiki_read import list_wiki_needs_review
from api.db.queries.wiki_write import upsert_wiki_page
from api.embeddings import EMBED_DIM


async def _noop_embed(texts: list[str]) -> list[list[float]]:
    return [[0.0] * EMBED_DIM for _ in texts]


async def _new_campaign(session_factory, user_id: str) -> str:
    async with session_factory() as db:
        async with db.begin():
            result = await db.execute(
                text("""
                    INSERT INTO synthesis_campaigns
                        (created_by, session_id, target_smiles, status)
                    VALUES (:uid, :sid, 'CCO', 'running')
                    RETURNING id::text
                """),
                {"uid": user_id, "sid": f"sess-{uuid.uuid4().hex[:12]}"},
            )
            return result.scalar_one()


# ── query-level tests (async, no TestClient) ─────────────────────────────────


@pytest.mark.asyncio
async def test_list_wiki_needs_review_owner_scoped(session_factory, user_id):
    """A draft created by user A must not appear in user B's needs-review list."""
    attacker = f"u-{uuid.uuid4().hex[:8]}"
    slug = f"draft-{uuid.uuid4().hex[:8]}"
    async with session_factory() as db:
        await upsert_wiki_page(
            db,
            slug=slug,
            title="Draft",
            content={"type": "doc", "content": []},
            content_text="Some long enough draft content to chunk over fifty characters easily.",
            created_by=user_id,
            citations=[],
            embed_fn=_noop_embed,
            needs_review=True,
        )

    async with session_factory() as db:
        owner_inbox = await list_wiki_needs_review(db, user_id)
    assert any(p["slug"] == slug for p in owner_inbox)

    async with session_factory() as db:
        attacker_inbox = await list_wiki_needs_review(db, attacker)
    assert not any(p["slug"] == slug for p in attacker_inbox)


@pytest.mark.asyncio
async def test_list_wiki_needs_review_excludes_archived(session_factory, user_id):
    """Pages marked archived must not surface in the inbox even when needs_review."""
    archived_slug = f"a-{uuid.uuid4().hex[:8]}"
    async with session_factory() as db:
        async with db.begin():
            await db.execute(
                text("""
                    INSERT INTO wiki_pages (slug, title, content, content_text,
                                            created_by, updated_by, needs_review, archived)
                    VALUES (:slug, 'Archived', CAST('{}' AS jsonb), 'x',
                            :uid, :uid, true, true)
                """),
                {"slug": archived_slug, "uid": user_id},
            )

    async with session_factory() as db:
        inbox = await list_wiki_needs_review(db, user_id)
    assert all(p["slug"] != archived_slug for p in inbox)


@pytest.mark.asyncio
async def test_list_wiki_needs_review_excludes_clean_pages(session_factory, user_id):
    """A page with needs_review=false should not appear."""
    clean_slug = f"clean-{uuid.uuid4().hex[:8]}"
    async with session_factory() as db:
        async with db.begin():
            await db.execute(
                text("""
                    INSERT INTO wiki_pages (slug, title, content, content_text,
                                            created_by, updated_by, needs_review)
                    VALUES (:slug, 'Clean', CAST('{}' AS jsonb), 'x',
                            :uid, :uid, false)
                """),
                {"slug": clean_slug, "uid": user_id},
            )

    async with session_factory() as db:
        inbox = await list_wiki_needs_review(db, user_id)
    assert all(p["slug"] != clean_slug for p in inbox)


# ── endpoint-level test (sync, runs the seeding via asyncio.run) ─────────────


def test_curator_inbox_endpoint_aggregates_buckets(client, auth_header):
    """End-to-end: seed one item in each bucket, verify the HTTP response.

    Builds its own AsyncEngine + sessionmaker rather than depending on the
    pytest-asyncio session_factory fixture, because mixing an async fixture
    with a sync test runs into pytest-asyncio plumbing issues.
    """
    import os

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    user_id = auth_header["Authorization"].removeprefix("Bearer mock:")
    wiki_slug = f"ix-{uuid.uuid4().hex[:8]}"

    async def _seed() -> tuple[str, str]:
        engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            return await _seed_inner(factory, user_id, wiki_slug)
        finally:
            await engine.dispose()

    async def _seed_inner(factory, user_id, wiki_slug) -> tuple[str, str]:
        # 1. Wiki page needing review.
        async with factory() as db:
            await upsert_wiki_page(
                db,
                slug=wiki_slug,
                title="Inbox draft",
                content={"type": "doc", "content": []},
                content_text="Inbox draft body, long enough to chunk over fifty characters.",
                created_by=user_id,
                citations=[],
                embed_fn=_noop_embed,
                needs_review=True,
            )
        # 2. Campaign step awaiting approval.
        cid = await _new_campaign(factory, user_id)
        async with factory() as db:
            async with db.begin():
                await add_campaign_step(
                    db, cid, 0, "C>>C", "test", status="pending_approval"
                )
        # 3. Look up page id for the contradiction.
        async with factory() as db:
            row = await db.execute(
                text("SELECT id::text FROM wiki_pages WHERE slug = :slug"),
                {"slug": wiki_slug},
            )
            page_id = row.scalar_one()
        async with factory() as db:
            await create_contradiction(
                db, page_id, "cit-a", "cit-b", "inconclusive", "test conflict"
            )
        return cid, page_id

    cid, page_id = asyncio.run(_seed())

    resp = client.get("/api/curator/inbox", headers=auth_header)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert "wiki_needs_review" in body
    assert "step_approvals" in body
    assert "contradictions" in body
    assert any(p["slug"] == wiki_slug for p in body["wiki_needs_review"])
    assert any(s["campaign_id"] == cid for s in body["step_approvals"])
    assert any(c["page_id"] == page_id for c in body["contradictions"])
    assert body["total_pending"] >= 3


def test_curator_inbox_requires_auth(client):
    """Unauthenticated request must 401 — owner-scoped surface."""
    resp = client.get("/api/curator/inbox")
    assert resp.status_code == 401
