"""Tests for the reaction-condition prediction cache.

Covers Phase A (`find_neighbor_conditions`) and Phase C
(`reaction_condition_predictions` cache + feedback link).

Like the rest of the test suite, these run against the CI Postgres
container with migrations applied first (.github/workflows/ci.yml).
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from api.db.queries.fp_utils import bit_string_to_pg_bytes
from api.db.queries.reaction_conditions import (
    get_cached_prediction,
    insert_prediction,
    list_predictions_for_reaction,
    record_used_prediction,
)
from api.db.queries.reactions import find_neighbor_conditions

# ── Helpers ──────────────────────────────────────────────────────────────────

def _drfp_bits(seed: int = 0) -> str:
    """Deterministic 2048-bit string. seed controls bit pattern so we can
    build two distinct fingerprints with known Tanimoto similarity to a
    query. seed=0 → all zeros except low byte; seed=1 → all ones."""
    if seed == 0:
        return "0" * 2047 + "1"
    return "1" * 2048


async def _insert_reaction_row(
    session_factory, rxn_smiles: str, conditions: str | None, drfp: str, user_id: str
) -> str:
    """Insert a reaction with a 2048-bit DRFP. asyncpg's binary protocol
    refuses to bind a Python str to a bit(2048) column even when wrapped
    in CAST() — pass the packed bytes representation instead."""
    drfp_bytes = bit_string_to_pg_bytes(drfp)
    async with session_factory() as db, db.begin():
        result = await db.execute(
            text("""
                    INSERT INTO reactions (rxn_smiles, name, conditions, drfp, created_by)
                    VALUES (:smiles, :name, :cond, :bits, :uid)
                    RETURNING id::text
                """),
            {
                "smiles": rxn_smiles,
                "name": f"test-{uuid.uuid4().hex[:6]}",
                "cond": conditions,
                "bits": drfp_bytes,
                "uid": user_id,
            },
        )
        return result.scalar_one()


# ── Phase A: neighbor conditions ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_find_neighbor_conditions_filters_null_and_blank(session_factory, user_id):
    """Only neighbors with a non-empty conditions string come back."""
    bits = _drfp_bits(seed=1)  # all-ones fingerprint
    # Three reactions with the same DRFP — perfect Tanimoto with the query.
    # One has conditions, one is NULL, one is whitespace.
    await _insert_reaction_row(session_factory, "CC>>CCC", "DMF, K2CO3, 80C", bits, user_id)
    await _insert_reaction_row(session_factory, "CN>>CNC", None, bits, user_id)
    await _insert_reaction_row(session_factory, "CO>>COC", "   ", bits, user_id)

    async with session_factory() as db:
        results = await find_neighbor_conditions(db, bits, limit=20, min_similarity=0.4)

    conditions = [r["conditions"] for r in results]
    assert "DMF, K2CO3, 80C" in conditions
    assert None not in conditions
    assert "   " not in conditions


# ── Phase C: prediction cache ────────────────────────────────────────────────

_CONDITIONS_PAYLOAD = {
    "catalysts": ["Pd(OAc)2"],
    "solvents": ["DMF"],
    "reagents": ["K2CO3"],
    "temperature_c": 80.0,
}


@pytest.mark.asyncio
async def test_insert_and_fetch_cached_prediction_with_reaction_id(session_factory, user_id):
    """The (reaction_id, model) dedupe path returns the inserted row."""
    rxn_id = await _insert_reaction_row(
        session_factory, "CC>>CCC", None, _drfp_bits(seed=1), user_id
    )
    async with session_factory() as db, db.begin():
        pid = await insert_prediction(
            db,
            rxn_smiles="CC>>CCC",
            conditions=_CONDITIONS_PAYLOAD,
            model="rxn4chemistry:v1",
            source="rxn4chemistry",
            created_by=user_id,
            confidence=0.87,
            reaction_id=rxn_id,
        )
    assert pid

    async with session_factory() as db:
        cached = await get_cached_prediction(
            db, rxn_smiles="CC>>CCC", model="rxn4chemistry:v1", reaction_id=rxn_id
        )
    assert cached is not None
    assert cached["conditions"]["catalysts"] == ["Pd(OAc)2"]
    assert cached["conditions"]["temperature_c"] == 80.0
    assert cached["model"] == "rxn4chemistry:v1"
    assert cached["source"] == "rxn4chemistry"


@pytest.mark.asyncio
async def test_insert_prediction_upserts_on_reaction_id_model(session_factory, user_id):
    """A second insert with the same (reaction_id, model) updates conditions
    rather than creating a duplicate row."""
    rxn_id = await _insert_reaction_row(
        session_factory, "CCO>>CCOC", None, _drfp_bits(seed=1), user_id
    )
    async with session_factory() as db, db.begin():
        await insert_prediction(
            db, rxn_smiles="CCO>>CCOC", conditions=_CONDITIONS_PAYLOAD,
            model="m1", source="rxn4chemistry", created_by=user_id, reaction_id=rxn_id,
        )
    async with session_factory() as db, db.begin():
        await insert_prediction(
            db, rxn_smiles="CCO>>CCOC",
            conditions={**_CONDITIONS_PAYLOAD, "temperature_c": 25.0},
            model="m1", source="rxn4chemistry", created_by=user_id, reaction_id=rxn_id,
        )
    async with session_factory() as db:
        rows = await list_predictions_for_reaction(db, rxn_id)
    assert len(rows) == 1
    assert rows[0]["conditions"]["temperature_c"] == 25.0


@pytest.mark.asyncio
async def test_cache_lookup_by_rxn_smiles_no_reaction_id(session_factory, user_id):
    """Predictions without a reaction_id (e.g. retrosynthesis intermediates)
    are found by the rxn_smiles fallback path."""
    async with session_factory() as db, db.begin():
        await insert_prediction(
            db, rxn_smiles="N#C>>N=C", conditions=_CONDITIONS_PAYLOAD,
            model="m2", source="rxn4chemistry", created_by=user_id,
            reaction_id=None,
        )
    async with session_factory() as db:
        cached = await get_cached_prediction(db, rxn_smiles="N#C>>N=C", model="m2")
    assert cached is not None
    assert cached["model"] == "m2"


@pytest.mark.asyncio
async def test_record_used_prediction_source_state_predicate(session_factory, user_id):
    """`used_in_step_id` only sets once — a second call with a different
    step_id is a no-op (returns False)."""
    rxn_id = await _insert_reaction_row(
        session_factory, "CC=O>>CC=N", None, _drfp_bits(seed=1), user_id
    )
    # Need a campaign + step to satisfy the FK.
    async with session_factory() as db, db.begin():
        campaign_id = (await db.execute(
            text("""
                    INSERT INTO synthesis_campaigns (created_by, session_id, target_smiles, status)
                    VALUES (:uid, :sid, :target, 'planning')
                    RETURNING id::text
                """),
            {"uid": user_id, "sid": f"sess-{uuid.uuid4().hex[:8]}", "target": "CCO"},
        )).scalar_one()
        step1_id = (await db.execute(
            text("""
                    INSERT INTO campaign_steps (campaign_id, step_idx, reaction_smiles, status)
                    VALUES (CAST(:cid AS uuid), 0, 'CC=O>>CC=N', 'pending')
                    RETURNING id::text
                """),
            {"cid": campaign_id},
        )).scalar_one()
        step2_id = (await db.execute(
            text("""
                    INSERT INTO campaign_steps (campaign_id, step_idx, reaction_smiles, status)
                    VALUES (CAST(:cid AS uuid), 1, 'CC=O>>CC=N', 'pending')
                    RETURNING id::text
                """),
            {"cid": campaign_id},
        )).scalar_one()
        pid = await insert_prediction(
            db, rxn_smiles="CC=O>>CC=N", conditions=_CONDITIONS_PAYLOAD,
            model="m3", source="rxn4chemistry", created_by=user_id,
            reaction_id=rxn_id,
        )

    async with session_factory() as db, db.begin():
        first = await record_used_prediction(db, pid, step1_id)
    assert first is True

    async with session_factory() as db, db.begin():
        second = await record_used_prediction(db, pid, step2_id)
    assert second is False  # source-state predicate prevents the clobber
