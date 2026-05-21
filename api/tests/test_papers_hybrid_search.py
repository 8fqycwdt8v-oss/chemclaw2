"""End-to-end DB integration tests for the paper_qa retrieval path.

Exercises `chunk_paper_text → insert_paper_chunks → hybrid_search_paper_chunks`
against the CI Postgres. RCS scoring (`score_chunks_with_llm`) calls real
LLM APIs and is excluded here — its parser is covered by
test_paper_rcs_json.py, the provider switch by manual smoke.

Embeddings are stubbed via a deterministic vector encoder: each chunk
gets a 1536-dim vector with a single "1.0" at a position derived from
the chunk's text — easy to construct retrieval scenarios where one
chunk is semantically closer to a query than another.
"""
from __future__ import annotations

import hashlib
import uuid

from api.db.queries.knowledge import upsert_paper
from api.db.queries.papers import (
    EMBED_DIM,
    hybrid_search_paper_chunks,
    insert_paper_chunks,
    search_paper_chunks_fts,
    semantic_search_paper_chunks,
)


def _vec_for(text: str) -> list[float]:
    """Deterministic 1536-dim vector with a single nonzero coordinate
    derived from the SHA1 of the text. Two chunks with the same text
    collide; otherwise they're orthogonal in expectation."""
    pos = int(hashlib.sha1(text.encode()).hexdigest()[:8], 16) % EMBED_DIM
    v = [0.0] * EMBED_DIM
    v[pos] = 1.0
    return v


async def _seed_paper(session_factory, user_id: str, *, title: str, chunks: list[str]) -> str:
    """Insert a paper + chunks, return paper_id. Each chunk gets a
    deterministic embedding via `_vec_for`."""
    async with session_factory() as db:
        paper_id, _ = await upsert_paper(
            db, url=f"https://example.com/{uuid.uuid4().hex[:8]}",
            title=title, created_by=user_id,
        )
    async with session_factory() as db:
        await insert_paper_chunks(
            db, paper_id,
            [
                {"chunk_idx": i, "section": None, "page": None,
                 "text": text, "embedding": _vec_for(text)}
                for i, text in enumerate(chunks)
            ],
        )
    return paper_id


async def test_fts_search_matches_keyword(session_factory, user_id: str) -> None:
    await _seed_paper(
        session_factory, user_id, title="JAK1 paper",
        chunks=[
            "JAK1 is a tyrosine kinase implicated in cytokine signalling.",
            "Solvent compatibility studies were performed in THF and DMF.",
        ],
    )
    async with session_factory() as db:
        rows = await search_paper_chunks_fts(db, "tyrosine kinase")
    assert len(rows) >= 1
    assert any("JAK1" in r["text"] for r in rows)


async def test_semantic_search_returns_close_chunks(
    session_factory, user_id: str,
) -> None:
    """When the query embedding exactly matches a chunk's embedding, that
    chunk should rank first."""
    target_text = "Selective JAK1 inhibitor with reduced JAK2 liability."
    await _seed_paper(
        session_factory, user_id, title="JAK inhibitors",
        chunks=[
            target_text,
            "Process chemistry considerations for kinase inhibitor scale-up.",
        ],
    )
    query_vec = _vec_for(target_text)
    async with session_factory() as db:
        rows = await semantic_search_paper_chunks(db, query_vec, max_distance=0.9)
    assert len(rows) >= 1
    assert rows[0]["text"] == target_text
    assert rows[0]["distance"] < 0.01  # exact match → near-zero cosine distance


async def test_hybrid_rrf_boosts_dual_hits(session_factory, user_id: str) -> None:
    """A chunk that lands in BOTH the FTS and semantic top-k must rank
    above chunks that hit only one leg — the whole point of RRF fusion."""
    overlap_text = "ROCK inhibitor phagocytosis assay in retinal pigment epithelium."
    fts_only_text = "Phagocytosis is a well-known process in immune cells."
    sem_only_text = "ROCK inhibitor compound has structural similarity to glaucoma drug ripasudil."

    await _seed_paper(
        session_factory, user_id, title="dAMD candidate",
        chunks=[overlap_text, fts_only_text, sem_only_text],
    )
    query = "phagocytosis ROCK inhibitor"
    # Query embedding aligned with overlap_text + sem_only_text but FTS will
    # also match fts_only_text on "phagocytosis".
    query_vec = _vec_for(overlap_text)
    async with session_factory() as db:
        rows = await hybrid_search_paper_chunks(db, query, query_vec, limit=5)
    assert len(rows) >= 1
    # The dual-hit chunk should rank first.
    assert rows[0]["text"] == overlap_text
    # And it should have both fts_rank and sem_rank set.
    assert rows[0]["fts_rank"] is not None
    assert rows[0]["sem_rank"] is not None


async def test_hybrid_search_owner_does_not_leak_paper_ids(
    session_factory, user_id: str,
) -> None:
    """paper_chunks doesn't carry created_by — ownership inherits through
    the papers join. The query returns rows from every paper that
    matches; the ownership check happens at the tool layer. Verify the
    SQL itself returns the rows (the boundary is enforced upstream)."""
    other_user = f"u-{uuid.uuid4().hex[:8]}"
    await _seed_paper(
        session_factory, user_id, title="My paper",
        chunks=["mine: tyrosine kinase research notes"],
    )
    await _seed_paper(
        session_factory, other_user, title="Their paper",
        chunks=["theirs: tyrosine kinase reaction conditions"],
    )
    async with session_factory() as db:
        rows = await search_paper_chunks_fts(db, "tyrosine kinase")
    # Both papers' chunks come back at the SQL layer — ownership is the
    # caller's responsibility. Test pins this contract.
    assert sum("mine" in r["text"] for r in rows) >= 1
    assert sum("theirs" in r["text"] for r in rows) >= 1


async def test_empty_paper_chunks_returns_empty(
    session_factory, user_id: str,
) -> None:
    """When no paper has been ingested, every leg returns []. No errors."""
    # Don't seed anything for this user — but other tests in the file
    # may have. Run against a fresh impossible query.
    async with session_factory() as db:
        rows = await search_paper_chunks_fts(
            db, f"impossible-token-{uuid.uuid4().hex}",
        )
    assert rows == []


async def test_paper_chunk_with_null_embedding_only_fts_recall(
    session_factory, user_id: str,
) -> None:
    """A chunk inserted with embedding=NULL is reachable only via FTS,
    never via the semantic leg."""
    async with session_factory() as db:
        paper_id, _ = await upsert_paper(
            db, url=f"https://example.com/{uuid.uuid4().hex[:8]}",
            title="No embedding", created_by=user_id,
        )
    async with session_factory() as db:
        await insert_paper_chunks(
            db, paper_id,
            [
                {"chunk_idx": 0, "section": None, "page": None,
                 "text": "Embedding-less chunk for FTS-only retrieval test.",
                 "embedding": None},
            ],
        )
    async with session_factory() as db:
        fts_rows = await search_paper_chunks_fts(
            db, "embedding-less retrieval",
        )
        sem_rows = await semantic_search_paper_chunks(
            db, _vec_for("Embedding-less chunk for FTS-only retrieval test."),
        )
    assert any("Embedding-less" in r["text"] for r in fts_rows)
    assert not any("Embedding-less" in r["text"] for r in sem_rows)


async def test_insert_paper_chunks_idempotent_via_conflict(
    session_factory, user_id: str,
) -> None:
    """Re-ingest of the same paper updates rather than duplicates — the
    ON CONFLICT clause in insert_paper_chunks."""
    paper_id = await _seed_paper(
        session_factory, user_id, title="Re-ingest",
        chunks=["original chunk text"],
    )
    async with session_factory() as db:
        await insert_paper_chunks(
            db, paper_id,
            [
                {"chunk_idx": 0, "section": "updated",
                 "page": 1, "text": "updated chunk text",
                 "embedding": _vec_for("updated chunk text")},
            ],
        )
    async with session_factory() as db:
        rows = await search_paper_chunks_fts(db, "updated")
    matches = [r for r in rows if r["paper_id"] == paper_id]
    assert len(matches) == 1
    assert matches[0]["section"] == "updated"
    assert matches[0]["page"] == 1
    assert matches[0]["text"] == "updated chunk text"



async def test_paper_id_filter_scopes_to_single_paper(
    session_factory, user_id: str,
) -> None:
    """`semantic_search_paper_chunks(..., paper_id=X)` and the FTS path
    both restrict to one paper. Without that, every retrieval would
    return matches from every ingested paper — useful for the global
    `paper_qa` flow, but the `paper_id` filter is what lets the agent
    drill into one specific document."""
    keyword = "binding affinity"
    target_id = await _seed_paper(
        session_factory, user_id, title="In-scope paper",
        chunks=[f"This compound shows {keyword} of 12 nM."],
    )
    other_id = await _seed_paper(
        session_factory, user_id, title="Out-of-scope paper",
        chunks=[f"Different compound, same {keyword} keyword though."],
    )

    # FTS leg with paper_id filter.
    async with session_factory() as db:
        rows = await search_paper_chunks_fts(db, keyword, paper_id=target_id)
    assert len(rows) >= 1
    assert all(r["paper_id"] == target_id for r in rows)
    assert other_id not in {r["paper_id"] for r in rows}

    # Semantic leg with paper_id filter (query vector aligned with the
    # in-scope chunk so it's the closer match by construction).
    query_vec = _vec_for(f"This compound shows {keyword} of 12 nM.")
    async with session_factory() as db:
        sem_rows = await semantic_search_paper_chunks(
            db, query_vec, paper_id=target_id,
        )
    assert len(sem_rows) >= 1
    assert all(r["paper_id"] == target_id for r in sem_rows)
    assert other_id not in {r["paper_id"] for r in sem_rows}

    # Sanity: without the filter, BOTH papers' chunks come back.
    async with session_factory() as db:
        unscoped = await search_paper_chunks_fts(db, keyword)
    paper_ids = {r["paper_id"] for r in unscoped}
    assert {target_id, other_id} <= paper_ids


async def test_hybrid_search_paper_id_filter(
    session_factory, user_id: str,
) -> None:
    """The hybrid (RRF) layer must also pass paper_id through to both legs."""
    target_id = await _seed_paper(
        session_factory, user_id, title="Hybrid target",
        chunks=["The Pd-catalyzed cross-coupling proceeded in 72% yield."],
    )
    await _seed_paper(
        session_factory, user_id, title="Hybrid distractor",
        chunks=["Distractor: another Pd-catalyzed reaction described elsewhere."],
    )
    query = "Pd-catalyzed yield"
    query_vec = _vec_for("The Pd-catalyzed cross-coupling proceeded in 72% yield.")
    async with session_factory() as db:
        rows = await hybrid_search_paper_chunks(
            db, query, query_vec, limit=5, paper_id=target_id,
        )
    assert len(rows) >= 1
    assert all(r["paper_id"] == target_id for r in rows)
