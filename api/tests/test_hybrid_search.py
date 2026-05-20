"""Tests for `hybrid_search_wiki` Reciprocal Rank Fusion logic.

The DB-execute paths in `search_wiki_by_fts` and `semantic_search_wiki`
are mocked so this test is pure-unit — no Postgres required. We're
locking in the RRF math + deduplication + signal-preservation, not the
underlying SQL (covered separately by test_wiki_queries.py).
"""
from __future__ import annotations

from typing import Any

import pytest

from api.db.queries import wiki_read


@pytest.mark.asyncio
async def test_rrf_fuses_disjoint_results(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pages that appear only in one leg still surface in the fused list,
    with `fts_rank` (or `sem_rank`) set and the other None."""

    async def _fts(*a: Any, **kw: Any) -> list[dict[str, Any]]:
        return [
            {"slug": "alpha", "title": "Alpha", "content_text": "lexical match"},
            {"slug": "beta", "title": "Beta", "content_text": "another"},
        ]

    async def _sem(*a: Any, **kw: Any) -> list[dict[str, Any]]:
        return [
            {"slug": "gamma", "title": "Gamma", "text": "semantic chunk"},
        ]

    monkeypatch.setattr(wiki_read, "search_wiki_by_fts", _fts)
    monkeypatch.setattr(wiki_read, "semantic_search_wiki", _sem)

    out = await wiki_read.hybrid_search_wiki(
        db=None,  # type: ignore[arg-type]  # both legs are mocked, db is unused
        query="anything", embedding=[0.0] * 1536, limit=5,
    )

    slugs = {r["slug"] for r in out}
    assert slugs == {"alpha", "beta", "gamma"}
    by_slug = {r["slug"]: r for r in out}
    assert by_slug["alpha"]["fts_rank"] == 1 and by_slug["alpha"]["sem_rank"] is None
    assert by_slug["beta"]["fts_rank"] == 2 and by_slug["beta"]["sem_rank"] is None
    assert by_slug["gamma"]["sem_rank"] == 1 and by_slug["gamma"]["fts_rank"] is None


@pytest.mark.asyncio
async def test_rrf_boosts_overlap(monkeypatch: pytest.MonkeyPatch) -> None:
    """A page that lands in both leg's top-N should outrank one that lands
    in only one leg's top-N — the whole point of fusion."""

    async def _fts(*a: Any, **kw: Any) -> list[dict[str, Any]]:
        return [
            {"slug": "fts-only", "title": "FtsOnly"},
            {"slug": "both", "title": "Both"},
        ]

    async def _sem(*a: Any, **kw: Any) -> list[dict[str, Any]]:
        return [
            {"slug": "sem-only", "title": "SemOnly", "text": "x"},
            {"slug": "both", "title": "Both", "text": "y"},
        ]

    monkeypatch.setattr(wiki_read, "search_wiki_by_fts", _fts)
    monkeypatch.setattr(wiki_read, "semantic_search_wiki", _sem)

    out = await wiki_read.hybrid_search_wiki(
        db=None,  # type: ignore[arg-type]  # both legs are mocked, db is unused
        query="anything", embedding=[0.0] * 1536, limit=5,
    )

    # "both" should be first; the singletons follow.
    assert out[0]["slug"] == "both"
    assert out[0]["fts_rank"] == 2 and out[0]["sem_rank"] == 2
    # Both singletons have the same RRF score (1 / (60 + 1)) but the
    # ordering of the others is implementation-defined; just verify both
    # are present.
    assert {out[1]["slug"], out[2]["slug"]} == {"fts-only", "sem-only"}


@pytest.mark.asyncio
async def test_rrf_preserves_signal_fields_from_both_legs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When a page appears in both legs, the fused row should carry the
    FTS leg's `content_text` AND the semantic leg's chunk `text` — both
    are useful for snippet generation."""

    async def _fts(*a: Any, **kw: Any) -> list[dict[str, Any]]:
        return [{"slug": "p1", "title": "P1", "content_text": "full body"}]

    async def _sem(*a: Any, **kw: Any) -> list[dict[str, Any]]:
        return [{"slug": "p1", "title": "P1", "text": "matching chunk"}]

    monkeypatch.setattr(wiki_read, "search_wiki_by_fts", _fts)
    monkeypatch.setattr(wiki_read, "semantic_search_wiki", _sem)

    out = await wiki_read.hybrid_search_wiki(
        db=None,  # type: ignore[arg-type]
        query="x", embedding=[0.0] * 1536, limit=5,
    )

    assert len(out) == 1
    assert out[0]["content_text"] == "full body"
    assert out[0]["text"] == "matching chunk"


@pytest.mark.asyncio
async def test_rrf_caps_results_at_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The fused output respects the `limit` cap even when both legs return
    more candidates."""

    async def _fts(*a: Any, **kw: Any) -> list[dict[str, Any]]:
        return [{"slug": f"f{i}", "title": f"F{i}"} for i in range(20)]

    async def _sem(*a: Any, **kw: Any) -> list[dict[str, Any]]:
        return [{"slug": f"s{i}", "title": f"S{i}", "text": ""} for i in range(20)]

    monkeypatch.setattr(wiki_read, "search_wiki_by_fts", _fts)
    monkeypatch.setattr(wiki_read, "semantic_search_wiki", _sem)

    out = await wiki_read.hybrid_search_wiki(
        db=None,  # type: ignore[arg-type]
        query="x", embedding=[0.0] * 1536, limit=7,
    )
    assert len(out) == 7


@pytest.mark.asyncio
async def test_rrf_normalizes_id_from_page_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Semantic-only hits come back keyed by `page_id`; FTS uses `id`.
    The fused row must surface a single `id` field regardless of which
    leg produced it — otherwise API consumers have to handle both."""

    async def _fts(*a: Any, **kw: Any) -> list[dict[str, Any]]:
        return [{"id": "uuid-1", "slug": "fts-page", "title": "F"}]

    async def _sem(*a: Any, **kw: Any) -> list[dict[str, Any]]:
        return [{"page_id": "uuid-2", "slug": "sem-page", "title": "S", "text": ""}]

    monkeypatch.setattr(wiki_read, "search_wiki_by_fts", _fts)
    monkeypatch.setattr(wiki_read, "semantic_search_wiki", _sem)

    out = await wiki_read.hybrid_search_wiki(
        db=None,  # type: ignore[arg-type]
        query="x", embedding=[0.0] * 1536, limit=5,
    )
    by_slug = {r["slug"]: r for r in out}
    assert by_slug["fts-page"]["id"] == "uuid-1"
    assert by_slug["sem-page"]["id"] == "uuid-2"


@pytest.mark.asyncio
async def test_rrf_empty_legs_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _empty(*a: Any, **kw: Any) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(wiki_read, "search_wiki_by_fts", _empty)
    monkeypatch.setattr(wiki_read, "semantic_search_wiki", _empty)

    out = await wiki_read.hybrid_search_wiki(
        db=None,  # type: ignore[arg-type]
        query="x", embedding=[0.0] * 1536, limit=10,
    )
    assert out == []
