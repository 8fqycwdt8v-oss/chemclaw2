"""HTTP-layer integration tests for V2 routes.

Covers the routes shipped in PRs #103-107 (campaign step approval,
hybrid wiki search, document upload). CI previously only exercised
the underlying query functions via session_factory — these tests
verify the actual HTTP behaviour: dependency resolution, response
shape, status codes, auth.

Pure async: every test uses httpx.AsyncClient + ASGITransport so the
whole thing runs on one event loop. Earlier attempts at sync tests
that called asyncio.run() inside pytest-asyncio's "auto" mode tripped
"Event loop is closed" errors as asyncpg's background cleanup tasks
outlived the per-test loop.
"""
from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from api.db.queries.campaigns import add_campaign_step
from api.db.queries.wiki_write import upsert_wiki_page
from api.embeddings import EMBED_DIM


async def _noop_embed(texts: list[str]) -> list[list[float]]:
    return [[0.0] * EMBED_DIM for _ in texts]


def _user_from_header(auth_header: dict[str, str]) -> str:
    return auth_header["Authorization"].removeprefix("Bearer mock:")


def _async_client(app) -> AsyncClient:
    """Build an httpx.AsyncClient that dispatches to the FastAPI app
    via ASGI in-process. No socket, no event-loop ownership issues."""
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _seed_campaign_with_pending_step(
    session_factory, user_id: str, step_idx: int = 0,
) -> str:
    """Create a campaign owned by user_id with one step in pending_approval."""
    async with session_factory() as db:
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


async def _seed_wiki_page(session_factory, user_id: str, slug: str, content_text: str) -> None:
    async with session_factory() as db:
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


# ── Campaign step approval ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_approve_step_owner_succeeds(client, app, session_factory, auth_header):
    user_id = _user_from_header(auth_header)
    cid = await _seed_campaign_with_pending_step(session_factory, user_id)

    async with _async_client(app) as ac:
        resp = await ac.post(
            f"/api/campaigns/{cid}/steps/0/approve", headers=auth_header,
        )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}


@pytest.mark.asyncio
async def test_approve_step_rejects_non_owner(client, app, session_factory, auth_header):
    """A user can't approve another user's step. Owner-scoping in queries
    layer plus 404 envelope at the route — verify the HTTP shape."""
    owner = f"u-{uuid.uuid4().hex[:8]}"
    cid = await _seed_campaign_with_pending_step(session_factory, owner)

    async with _async_client(app) as ac:
        resp = await ac.post(
            f"/api/campaigns/{cid}/steps/0/approve", headers=auth_header,
        )
    assert resp.status_code == 404
    assert "not awaiting approval" in resp.text or "not owned" in resp.text


@pytest.mark.asyncio
async def test_approve_step_invalid_uuid(client, app, auth_header):
    """The route validates campaign_id UUID format before touching the DB."""
    async with _async_client(app) as ac:
        resp = await ac.post(
            "/api/campaigns/not-a-uuid/steps/0/approve", headers=auth_header,
        )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_approve_step_requires_auth(client, app):
    fake_uuid = str(uuid.uuid4())
    async with _async_client(app) as ac:
        resp = await ac.post(f"/api/campaigns/{fake_uuid}/steps/0/approve")
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_reject_step_owner_succeeds(client, app, session_factory, auth_header):
    user_id = _user_from_header(auth_header)
    cid = await _seed_campaign_with_pending_step(session_factory, user_id)

    async with _async_client(app) as ac:
        resp = await ac.post(
            f"/api/campaigns/{cid}/steps/0/reject", headers=auth_header,
        )
    assert resp.status_code == 200, resp.text
    assert resp.json() == {"ok": True}


# ── Hybrid wiki search ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_default_mode_is_hybrid(client, app, session_factory, auth_header, monkeypatch):
    """GET /api/search?q=... defaults to mode=hybrid and returns the
    documented response shape.

    Stubs `embed_texts` so the test doesn't hit OpenAI; CI's placeholder
    OPENAI_API_KEY wouldn't authenticate against real embeddings."""
    async def _stub_embed(texts: list[str]) -> list[list[float]]:
        return [[0.0] * EMBED_DIM for _ in texts]

    # search.py does the embed_texts import lazily inside the function,
    # so patching the canonical source is enough — the lazy import re-
    # resolves it on every call.
    monkeypatch.setattr("api.embeddings.embed_texts", _stub_embed)

    user_id = _user_from_header(auth_header)
    slug = f"hybrid-{uuid.uuid4().hex[:8]}"
    await _seed_wiki_page(
        session_factory, user_id, slug,
        "Suzuki coupling is a palladium-catalyzed cross-coupling reaction "
        "used to form carbon-carbon bonds between aryl halides and boronic acids. "
        "It is one of the most reliable methods in modern synthesis.",
    )

    async with _async_client(app) as ac:
        resp = await ac.get("/api/search?q=Suzuki+coupling", headers=auth_header)
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


@pytest.mark.asyncio
async def test_search_fts_mode_explicit(client, app, auth_header):
    """Passing mode=fts opts back into the legacy text-only path —
    rows must NOT have the hybrid-specific `score` field."""
    async with _async_client(app) as ac:
        resp = await ac.get(
            "/api/search?q=palladium&mode=fts", headers=auth_header,
        )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["mode"] == "fts"
    assert "wiki" in body
    for row in body["wiki"]:
        assert "score" not in row  # FTS path doesn't add the fusion score


@pytest.mark.asyncio
async def test_search_unknown_mode_rejected(client, app, auth_header):
    """Mode is a Literal in the Pydantic param — unknown values 422."""
    async with _async_client(app) as ac:
        resp = await ac.get("/api/search?q=x&mode=bogus", headers=auth_header)
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_search_query_too_long(client, app, auth_header):
    async with _async_client(app) as ac:
        resp = await ac.get(
            f"/api/search?q={'x' * 600}", headers=auth_header,
        )
    assert resp.status_code == 422


# ── Document upload (basic mode) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_document_upload_plain_text(client, app, auth_header, monkeypatch):
    """Plain-text upload in basic mode: extracts text, registers paper,
    creates wiki page draft with needs_review=true.

    Stubs `embed_texts` (the route holds a module-level binding) and
    `fetch_crossref_metadata` so the test doesn't depend on network
    access from CI."""
    async def _stub_embed(texts: list[str]) -> list[list[float]]:
        return [[0.0] * EMBED_DIM for _ in texts]

    async def _stub_crossref(doi: str) -> None:
        return None

    monkeypatch.setattr("api.routes.integrations.embed_texts", _stub_embed)
    monkeypatch.setattr(
        "api.routes.integrations.fetch_crossref_metadata", _stub_crossref,
    )

    body = (
        "Title of the Paper\n\n"
        "This is a short body. See 10.1234/example.5678 for the original work."
    ).encode()
    async with _async_client(app) as ac:
        resp = await ac.post(
            "/api/integrations/documents",
            headers=auth_header,
            files={"file": ("test.txt", body, "text/plain")},
        )
    assert resp.status_code == 200, resp.text
    j = resp.json()
    assert j["chars"] > 0
    assert j["doi"] == "10.1234/example.5678"
    assert j["title"] == "Title of the Paper"
    assert j["wiki_slug"] and j["wiki_slug"].startswith("10-1234-")
    assert j["extracted_compounds"] == []
    assert j["extracted_citations"] == []
    assert j["resolved_smiles"] == []


@pytest.mark.asyncio
async def test_document_upload_unsupported_type(client, app, auth_header):
    async with _async_client(app) as ac:
        resp = await ac.post(
            "/api/integrations/documents",
            headers=auth_header,
            files={"file": ("x.bin", b"\x00\x01\x02", "application/octet-stream")},
        )
    assert resp.status_code == 415


@pytest.mark.asyncio
async def test_document_upload_oversize_rejected(client, app, auth_header):
    """Payloads over 10 MB are rejected without buffering the whole file."""
    big = b"a" * (11 * 1024 * 1024)  # 11 MB
    async with _async_client(app) as ac:
        resp = await ac.post(
            "/api/integrations/documents",
            headers=auth_header,
            files={"file": ("big.txt", big, "text/plain")},
        )
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_document_upload_requires_auth(client, app):
    async with _async_client(app) as ac:
        resp = await ac.post(
            "/api/integrations/documents",
            files={"file": ("x.txt", b"hello", "text/plain")},
        )
    assert resp.status_code == 401
