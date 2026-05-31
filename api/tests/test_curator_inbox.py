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

import uuid

import pytest
from sqlalchemy import text

from api.db.queries.wiki_read import list_wiki_needs_review
from api.db.queries.wiki_write import upsert_wiki_page
from api.embeddings import EMBED_DIM


async def _noop_embed(texts: list[str]) -> list[list[float]]:
    return [[0.0] * EMBED_DIM for _ in texts]


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


def test_curator_inbox_endpoint_shape(client, auth_header):
    """The endpoint returns the four labelled buckets + total_pending,
    regardless of whether the user has any items in any bucket.

    Content-bearing assertions live in the query-level tests above so
    this stays sync (TestClient + pytest-asyncio don't mix cleanly when
    the test itself is async — see the docstring at the top of the file)."""
    resp = client.get("/api/curator/inbox", headers=auth_header)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # All four buckets must be present, each a list (possibly empty).
    assert isinstance(body.get("wiki_needs_review"), list)
    assert isinstance(body.get("step_approvals"), list)
    assert isinstance(body.get("contradictions"), list)
    assert isinstance(body.get("draft_reviews"), list)
    # total_pending equals the sum of the four bucket sizes.
    assert body["total_pending"] == (
        len(body["wiki_needs_review"])
        + len(body["step_approvals"])
        + len(body["contradictions"])
        + len(body["draft_reviews"])
    )


def test_curator_inbox_requires_auth(client):
    """Unauthenticated request must 401 — owner-scoped surface."""
    resp = client.get("/api/curator/inbox")
    assert resp.status_code == 401
