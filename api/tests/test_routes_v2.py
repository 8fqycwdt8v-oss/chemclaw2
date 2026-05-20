"""HTTP-layer integration tests for V2 routes.

Covers the routes shipped in PRs #103-107 (campaign step approval,
hybrid wiki search, document upload). CI previously only exercised
the underlying query functions via session_factory — these tests
verify the actual HTTP behaviour: dependency resolution, response
shape, status codes, auth.

Sync style (no @pytest.mark.asyncio) because TestClient + pytest-
asyncio combine badly when the test itself is async. DB seeding for
these tests runs via asyncio.run() against a one-shot engine.
"""
from __future__ import annotations

import asyncio
import os
import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from api.db.queries.campaigns import add_campaign_step
from api.db.queries.wiki_write import upsert_wiki_page
from api.embeddings import EMBED_DIM


async def _noop_embed(texts: list[str]) -> list[list[float]]:
    return [[0.0] * EMBED_DIM for _ in texts]


def _run_async(coro):
    """Run an awaitable inside a synchronous test."""
    return asyncio.run(coro)


def _user_from_header(auth_header: dict[str, str]) -> str:
    return auth_header["Authorization"].removeprefix("Bearer mock:")


async def _seed_campaign_with_pending_step(user_id: str, step_idx: int = 0) -> str:
    """Create a campaign owned by user_id with one step in pending_approval."""
    engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            async with db.begin():
                row = await db.execute(
                    text(
                        "INSERT INTO synthesis_campaigns "
                        "(created_by, session_id, target_smiles, status) "
                        "VALUES (:uid, :sid, 'CCO', 'running') "
                        "RETURNING id::text"
                    ),
                    {"uid": user_id, "sid": f"sess-{uuid.uuid4().hex[:12]}"},
                )
                cid = row.scalar_one()
                await add_campaign_step(
                    db, cid, step_idx, "C>>C", "test", status="pending_approval"
                )
        return cid
    finally:
        await engine.dispose()


async def _seed_wiki_page(user_id: str, slug: str, content_text: str) -> None:
    """Upsert a wiki page using the no-op embedder."""
    engine = create_async_engine(os.environ["DATABASE_URL"], pool_pre_ping=True)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with factory() as db:
            await upsert_wiki_page(
                db,
                slug=slug,
                title=slug.replace("-", " ").title(),
                content={"type": "doc", "content": []},
                content_text=content_text,
                created_by=user_id,
                citations=[],
                embed_fn=_noop_embed,
            )
    finally:
        await engine.dispose()


# ── Campaign step approval ───────────────────────────────────────────────────


def test_approve_step_owner_succeeds(client, auth_header):
    user_id = _user_from_header(auth_header)
    cid = _run_async(_seed_campaign_with_pending_step(user_id))

    resp = client.post(
        f"/api/campaigns/{cid}/steps/0/approve",
        headers=auth_header,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}


def test_approve_step_rejects_non_owner(client, auth_header):
    """A user can't approve another user's step. Owner-scoping in queries
    layer plus 404 envelope at the route — verify the HTTP shape."""
    owner = f"u-{uuid.uuid4().hex[:8]}"
    cid = _run_async(_seed_campaign_with_pending_step(owner))

    # Caller from `auth_header` is NOT the owner.
    resp = client.post(
        f"/api/campaigns/{cid}/steps/0/approve",
        headers=auth_header,
    )
    assert resp.status_code == 404
    assert "not awaiting approval" in resp.text or "not owned" in resp.text


def test_approve_step_invalid_uuid(client, auth_header):
    """The route validates campaign_id UUID format before touching the DB."""
    resp = client.post(
        "/api/campaigns/not-a-uuid/steps/0/approve",
        headers=auth_header,
    )
    assert resp.status_code == 400


def test_approve_step_requires_auth(client):
    fake_uuid = str(uuid.uuid4())
    resp = client.post(f"/api/campaigns/{fake_uuid}/steps/0/approve")
    assert resp.status_code == 401


def test_reject_step_owner_succeeds(client, auth_header):
    user_id = _user_from_header(auth_header)
    cid = _run_async(_seed_campaign_with_pending_step(user_id))

    resp = client.post(
        f"/api/campaigns/{cid}/steps/0/reject",
        headers=auth_header,
    )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}


# ── Hybrid wiki search ───────────────────────────────────────────────────────


def test_search_default_mode_is_hybrid(client, auth_header, monkeypatch):
    """GET /api/search?q=... defaults to mode=hybrid and returns the
    documented response shape.

    Stubs `embed_texts` so the test doesn't hit OpenAI; CI's placeholder
    OPENAI_API_KEY wouldn't authenticate against real embeddings."""
    async def _stub_embed(texts: list[str]) -> list[list[float]]:
        return [[0.0] * EMBED_DIM for _ in texts]

    # Patch both the canonical import site AND the route's import-inside-
    # function path: routes/search.py does `from api.embeddings import
    # embed_texts` lazily, so the bind site is api.embeddings.embed_texts.
    monkeypatch.setattr("api.embeddings.embed_texts", _stub_embed)

    user_id = _user_from_header(auth_header)
    slug = f"hybrid-{uuid.uuid4().hex[:8]}"
    _run_async(_seed_wiki_page(
        user_id, slug,
        "Suzuki coupling is a palladium-catalyzed cross-coupling reaction "
        "used to form carbon-carbon bonds between aryl halides and boronic acids. "
        "It is one of the most reliable methods in modern synthesis."
    ))

    resp = client.get("/api/search?q=Suzuki+coupling", headers=auth_header)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "hybrid"
    assert "wiki" in body
    assert isinstance(body["wiki"], list)
    # Every row in hybrid mode carries `score`, `fts_rank`, `sem_rank`.
    for row in body["wiki"]:
        assert "score" in row
        assert "fts_rank" in row
        assert "sem_rank" in row


def test_search_fts_mode_explicit(client, auth_header):
    """Passing mode=fts opts back into the legacy text-only path —
    rows must NOT have the hybrid-specific `score` field."""
    resp = client.get("/api/search?q=palladium&mode=fts", headers=auth_header)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "fts"
    assert "wiki" in body
    for row in body["wiki"]:
        assert "score" not in row  # FTS path doesn't add the fusion score


def test_search_unknown_mode_rejected(client, auth_header):
    """Mode is a Literal in the Pydantic param — unknown values 422."""
    resp = client.get("/api/search?q=x&mode=bogus", headers=auth_header)
    assert resp.status_code == 422


def test_search_query_too_long(client, auth_header):
    resp = client.get(f"/api/search?q={'x' * 600}", headers=auth_header)
    assert resp.status_code == 422


# ── Document upload (basic mode) ─────────────────────────────────────────────


def test_document_upload_plain_text(client, auth_header, monkeypatch):
    """Plain-text upload in basic mode: extracts text, registers paper,
    creates wiki page draft with needs_review=true.

    Stubs embed_texts (the wiki page draft upserts a page, which calls
    embed_fn) and fetch_crossref_metadata (so the test doesn't depend
    on network access from CI). The route's CrossRef path already
    fail-opens to None on network errors but stubbing makes the test
    deterministic regardless of CI's outbound connectivity policy."""
    async def _stub_embed(texts: list[str]) -> list[list[float]]:
        return [[0.0] * EMBED_DIM for _ in texts]

    async def _stub_crossref(doi: str) -> None:
        return None  # Force the first-line title heuristic.

    # routes/integrations.py does `from api.embeddings import embed_texts`
    # at module top, so the route holds a local binding — patch THERE
    # to override what the route actually calls.
    monkeypatch.setattr("api.routes.integrations.embed_texts", _stub_embed)
    monkeypatch.setattr(
        "api.routes.integrations.fetch_crossref_metadata",
        _stub_crossref,
    )

    body = (
        "Title of the Paper\n\n"
        "This is a short body. See 10.1234/example.5678 for the original work."
    ).encode()
    resp = client.post(
        "/api/integrations/documents",
        headers=auth_header,
        files={"file": ("test.txt", body, "text/plain")},
    )
    assert resp.status_code == 200, resp.text
    j = resp.json()
    assert j["chars"] > 0
    assert j["doi"] == "10.1234/example.5678"
    # First non-empty line is the title.
    assert j["title"] == "Title of the Paper"
    # Wiki slug derived from DOI.
    assert j["wiki_slug"] and j["wiki_slug"].startswith("10-1234-")
    # Basic mode → empty entity buckets.
    assert j["extracted_compounds"] == []
    assert j["extracted_citations"] == []
    assert j["resolved_smiles"] == []


def test_document_upload_unsupported_type(client, auth_header):
    resp = client.post(
        "/api/integrations/documents",
        headers=auth_header,
        files={"file": ("x.bin", b"\x00\x01\x02", "application/octet-stream")},
    )
    assert resp.status_code == 415


def test_document_upload_oversize_rejected(client, auth_header):
    """Payloads over 10 MB are rejected without buffering the whole file."""
    big = b"a" * (11 * 1024 * 1024)  # 11 MB
    resp = client.post(
        "/api/integrations/documents",
        headers=auth_header,
        files={"file": ("big.txt", big, "text/plain")},
    )
    assert resp.status_code == 413


def test_document_upload_requires_auth(client):
    resp = client.post(
        "/api/integrations/documents",
        files={"file": ("x.txt", b"hello", "text/plain")},
    )
    assert resp.status_code == 401


# ── Curator inbox — already covered in test_curator_inbox.py ─────────────────
# Listed here for the test plan; no duplication needed.
