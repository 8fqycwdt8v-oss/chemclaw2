"""Tests for the fingerprint similarity metric (Tanimoto/Jaccard).

Guards the fix in migrations 0045-0048 + api/db/queries/{compounds,reactions}.py:
the HNSW indexes on compounds.morgan_fp and reactions.drfp must use
bit_jaccard_ops (operator `<%>`, Jaccard distance = 1 - Tanimoto), NOT the
original bit_hamming_ops (`<~>`). Hamming pruned the ANN candidate pool by a
metric that did not match the Tanimoto rerank, silently dropping true
top-Tanimoto neighbours.

These run against the real Postgres container CI provides (see conftest).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from api.db.queries.compounds import find_similar_compounds
from api.db.queries.fp_utils import bit_string_to_pg_bytes


def _fp(set_bits: range | list[int]) -> str:
    """Build a 2048-char 0/1 fingerprint string with `set_bits` turned on."""
    bits = ["0"] * 2048
    for i in set_bits:
        bits[i] = "1"
    return "".join(bits)


async def _insert_compound(session_factory, user_id: str, smiles: str,
                           name: str, fp_bits: str) -> str:
    """Insert a compound with a specific Morgan fingerprint via raw SQL.

    Mirrors the seeding pattern in test_reaction_outcomes._insert_reaction:
    asyncpg rejects a str bind for bit(2048) even under CAST(), so the bits
    are packed to bytes with bit_string_to_pg_bytes.
    """
    async with session_factory() as db, db.begin():
        result = await db.execute(
            text("""
                    INSERT INTO compounds (smiles, name, created_by)
                    VALUES (:smi, :name, :uid)
                    RETURNING id::text
                """),
            {"smi": smiles, "name": name, "uid": user_id},
        )
        cid = result.scalar_one()
        await db.execute(
            text("""
                    UPDATE compounds
                    SET morgan_fp = CAST(:bits AS bit(2048)),
                        fp_computed_at = now()
                    WHERE id = CAST(:cid AS uuid)
                """),
            {"bits": bit_string_to_pg_bytes(fp_bits), "cid": cid},
        )
        return cid


@pytest.mark.asyncio
async def test_fingerprint_indexes_use_jaccard_opclass(session_factory) -> None:
    """The HNSW indexes must be built with bit_jaccard_ops, and the old
    bit_hamming_ops indexes must be gone."""
    async with session_factory() as db:
        rows = await db.execute(text("""
            SELECT indexname, indexdef
            FROM pg_indexes
            WHERE tablename IN ('compounds', 'reactions')
        """))
        defs = {r.indexname: r.indexdef for r in rows}

    assert "compounds_morgan_fp_jaccard_hnsw" in defs, defs.keys()
    assert "reactions_drfp_jaccard_hnsw" in defs, defs.keys()
    assert "bit_jaccard_ops" in defs["compounds_morgan_fp_jaccard_hnsw"]
    assert "bit_jaccard_ops" in defs["reactions_drfp_jaccard_hnsw"]

    # The superseded Hamming indexes must not linger.
    assert "compounds_morgan_fp_hnsw" not in defs
    assert "reactions_drfp_hnsw" not in defs


@pytest.mark.asyncio
async def test_similarity_search_ranks_and_thresholds_by_tanimoto(
    session_factory, user_id: str,
) -> None:
    """End-to-end: the `<%>` candidate query + Tanimoto rerank returns
    neighbours ordered by Tanimoto, with the min_tanimoto cutoff applied."""
    tag = uuid.uuid4().hex[:8]
    query = _fp(range(10))                 # bits 0..9
    cid_exact = await _insert_compound(
        session_factory, user_id, smiles=f"C-{tag}-exact",
        name=f"exact-{tag}", fp_bits=_fp(range(10)),     # Tanimoto 1.0
    )
    cid_partial = await _insert_compound(
        session_factory, user_id, smiles=f"C-{tag}-partial",
        name=f"partial-{tag}", fp_bits=_fp(range(8, 18)),   # overlap 2 / union 18 ~ 0.111
    )

    async with session_factory() as db:
        hits = await find_similar_compounds(
            db, query, limit=20, min_tanimoto=0.4,
        )

    by_id = {h["id"]: h for h in hits}
    # Exact match is well above the 0.4 cutoff; the partial (~0.11) is below it.
    assert cid_exact in by_id, "exact-Tanimoto neighbour must be returned"
    assert cid_partial not in by_id, "sub-threshold neighbour must be filtered out"
    assert by_id[cid_exact]["tanimoto"] == pytest.approx(1.0, abs=1e-9)


@pytest.mark.asyncio
async def test_similarity_search_orders_neighbours(
    session_factory, user_id: str,
) -> None:
    """A closer (higher-Tanimoto) neighbour ranks ahead of a farther one."""
    tag = uuid.uuid4().hex[:8]
    query = _fp(range(20))
    cid_near = await _insert_compound(
        session_factory, user_id, smiles=f"C-{tag}-near",
        name=f"near-{tag}", fp_bits=_fp(range(18)),   # high overlap
    )
    cid_far = await _insert_compound(
        session_factory, user_id, smiles=f"C-{tag}-far",
        name=f"far-{tag}", fp_bits=_fp(range(10, 30)),   # lower overlap
    )

    async with session_factory() as db:
        hits = await find_similar_compounds(
            db, query, limit=50, min_tanimoto=0.0,
        )

    order = [h["id"] for h in hits]
    assert cid_near in order and cid_far in order
    assert order.index(cid_near) < order.index(cid_far), (
        "higher-Tanimoto neighbour must rank ahead of the lower one"
    )
