"""DB tests for the shared ingest_document pipeline.

Runs against the CI Postgres container. Embeddings are stubbed so the wiki
upsert doesn't reach OpenAI; basic mode means no CrossRef / entity-extraction
network calls either.
"""
from __future__ import annotations

import pytest

from api.db.queries.wiki_read import get_wiki_page
from api.embeddings import EMBED_DIM
from api.integrations.ingest import ingest_document


async def _noop_embed(texts: list[str]) -> list[list[float]]:
    return [[0.0] * EMBED_DIM for _ in texts]


@pytest.mark.asyncio
async def test_ingest_basic_creates_surfaces(
    session_factory, monkeypatch, user_id
) -> None:
    monkeypatch.setattr("api.integrations.ingest.embed_texts", _noop_embed)
    content = b"# Catalytic Hydrogenation Notes\n\nPd/C reduces alkenes cleanly."
    async with session_factory() as db:
        result = await ingest_document(
            db,
            content=content,
            filename="notes.md",
            content_type="text/markdown",
            user_id=user_id,
            extract="basic",
        )
    assert result["fact_id"]
    assert result["title"]
    slug = result["wiki_slug"]
    assert slug

    async with session_factory() as db:
        page = await get_wiki_page(db, slug)
    assert page is not None
    assert page["title"] == result["title"]


@pytest.mark.asyncio
async def test_ingest_is_idempotent_on_same_bytes(
    session_factory, monkeypatch, user_id
) -> None:
    monkeypatch.setattr("api.integrations.ingest.embed_texts", _noop_embed)
    content = b"# Idempotent Doc\n\nSame bytes, same source_id."

    async def _run() -> dict:
        async with session_factory() as db:
            return await ingest_document(
                db,
                content=content,
                filename="dup.md",
                content_type="text/markdown",
                user_id=user_id,
                extract="basic",
            )

    first = await _run()
    second = await _run()
    # Content-hash source_id → stable slug + fact id; the second pass updates
    # rather than creating a duplicate.
    assert first["wiki_slug"] == second["wiki_slug"]
    assert first["fact_id"] == second["fact_id"]


@pytest.mark.asyncio
async def test_ingest_extracts_docx(session_factory, monkeypatch, user_id) -> None:
    import io

    from docx import Document

    monkeypatch.setattr("api.integrations.ingest.embed_texts", _noop_embed)
    doc = Document()
    doc.add_paragraph("Suzuki coupling optimisation summary.")
    buf = io.BytesIO()
    doc.save(buf)

    async with session_factory() as db:
        result = await ingest_document(
            db,
            content=buf.getvalue(),
            filename="summary.docx",
            content_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            user_id=user_id,
            extract="basic",
        )
    assert result["chars"] > 0
    assert "Suzuki coupling" in result["title"]
