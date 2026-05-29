"""DB tests for the KG-population step of ingest_document.

Stubs the KG-extraction LLM with canned facts/hypotheses and asserts they land
in world_model_entries (with source provenance + confidence) and hypotheses,
anchored to a corpus investigation. Also covers corpus get-or-create idempotency.
"""
from __future__ import annotations

import uuid

import pytest

from api.db.queries.investigations import get_or_create_corpus_investigation
from api.db.queries.world_model import list_world_model_entries
from api.embeddings import EMBED_DIM
from api.integrations.ingest import ingest_document


async def _noop_embed(texts: list[str]) -> list[list[float]]:
    return [[0.0] * EMBED_DIM for _ in texts]


async def _fake_kg(text, **kwargs):  # noqa: ANN001
    return {
        "facts": [
            {"content": "Pd/C reduces alkenes.", "kind": "fact", "confidence": 0.9,
             "context": "intro"},
            {"content": "Yield 92%.", "kind": "evidence", "confidence": 0.75},
        ],
        "hypotheses": [
            {"statement": "Pt/C improves selectivity.", "rationale": "by analogy"},
        ],
    }


@pytest.mark.asyncio
async def test_corpus_investigation_get_or_create_idempotent(
    session_factory, user_id
) -> None:
    title = f"Corpus {uuid.uuid4().hex}"
    async with session_factory() as db:
        a = await get_or_create_corpus_investigation(db, title, "obj", user_id)
    async with session_factory() as db:
        b = await get_or_create_corpus_investigation(db, title, "obj", user_id)
    assert a == b


@pytest.mark.asyncio
async def test_ingest_populates_kg(session_factory, monkeypatch, user_id) -> None:
    monkeypatch.setattr("api.integrations.ingest.embed_texts", _noop_embed)
    monkeypatch.setattr("api.integrations.ingest.extract_world_model", _fake_kg)

    async with session_factory() as db:
        inv_id = await get_or_create_corpus_investigation(
            db, f"Drive corpus {uuid.uuid4().hex}", "obj", user_id
        )

    content = b"# Hydrogenation\n\nPd/C reduces alkenes; yield 92%."
    async with session_factory() as db:
        result = await ingest_document(
            db,
            content=content,
            filename="h.md",
            content_type="text/markdown",
            user_id=user_id,
            extract="basic",
            extract_kg=True,
            investigation_id=inv_id,
        )
    assert result["kg"] == {"facts": 2, "hypotheses": 1}

    async with session_factory() as db:
        entries = await list_world_model_entries(db, inv_id, user_id)
    assert len(entries) == 2
    by_content = {e["content"]: e for e in entries}
    fact = by_content["Pd/C reduces alkenes."]
    assert fact["kind"] == "fact"
    assert fact["confidence"] == 0.9
    # Provenance points back to the source document.
    assert fact["payload"]["source"]["type"] == "document"
    assert fact["payload"]["source"]["wiki_slug"] == result["wiki_slug"]


@pytest.mark.asyncio
async def test_kg_skipped_without_flag(session_factory, monkeypatch, user_id) -> None:
    monkeypatch.setattr("api.integrations.ingest.embed_texts", _noop_embed)
    # extract_world_model must NOT be called when extract_kg is False.
    def _boom(*a, **k):  # noqa: ANN001, ANN202
        raise AssertionError("extract_world_model should not be called")
    monkeypatch.setattr("api.integrations.ingest.extract_world_model", _boom)

    async with session_factory() as db:
        result = await ingest_document(
            db,
            content=b"# Doc\n\nbody",
            filename="d.md",
            content_type="text/markdown",
            user_id=user_id,
            extract="basic",
        )
    assert result["kg"] == {"facts": 0, "hypotheses": 0}
