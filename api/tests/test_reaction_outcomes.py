"""Tests for reaction_outcomes queries + outcome-aware similarity join.

Exercises the vertical slice that feeds the process-gap-analyst sub-agent:
  - insert_outcome (manual + ELN paths, idempotency on eln_experiment_id)
  - list_outcomes_for_reaction
  - find_similar_reactions(..., include_outcomes=True) — outcomes ride
    along on the ranked hits
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from api.db.queries.fp_utils import bit_string_to_pg_bytes
from api.db.queries.reaction_outcomes import insert_outcome, list_outcomes_for_reaction
from api.db.queries.reactions import find_similar_reactions


# Two 2048-bit DRFP fingerprints, chosen to be near-identical (Tanimoto ~ 1)
# so the rerank keeps both above the default min_similarity=0.4 threshold.
_FP_ALL_ONES = "1" * 2048
_FP_MOSTLY_ONES = "1" * 2047 + "0"  # differs by 1 bit


async def _insert_reaction(
    session_factory,
    user_id: str,
    rxn_smiles: str,
    name: str | None = None,
    drfp_bits: str | None = None,
) -> str:
    """Insert a reaction (optionally with a 2048-bit DRFP) via raw SQL.

    The ORM helper in api/db/queries/reactions.py works fine for NULL
    drfp, but tests that want to seed a specific fingerprint need to
    bypass it: asyncpg's binary protocol rejects a Python str for
    bit(2048) parameter binds even when wrapped in CAST(); the canonical
    fix is to pack the bits to bytes via ``bit_string_to_pg_bytes``.
    """
    async with session_factory() as db:
        async with db.begin():
            result = await db.execute(
                text("""
                    INSERT INTO reactions (rxn_smiles, name, created_by)
                    VALUES (:smi, :name, :uid)
                    RETURNING id::text
                """),
                {"smi": rxn_smiles, "name": name, "uid": user_id},
            )
            rid = result.scalar_one()
            if drfp_bits is not None:
                await db.execute(
                    text("""
                        UPDATE reactions
                        SET drfp = CAST(:bits AS bit(2048)),
                            fp_computed_at = now()
                        WHERE id = CAST(:rid AS uuid)
                    """),
                    {"bits": bit_string_to_pg_bytes(drfp_bits), "rid": rid},
                )
            return rid


@pytest.mark.asyncio
async def test_insert_outcome_roundtrip(session_factory, user_id: str) -> None:
    reaction_id = await _insert_reaction(
        session_factory, user_id, rxn_smiles="CC>>CCO", name="test_rxn",
    )

    async with session_factory() as db:
        async with db.begin():
            outcome_id, already = await insert_outcome(
                db,
                reaction_id=reaction_id,
                source="manual",
                status="success",
                created_by=user_id,
                yield_pct=72.5,
                conditions_actual={"solvent": "toluene", "temp_c": 80},
                observations="Clean conversion",
            )

    assert outcome_id
    assert already is False

    async with session_factory() as db:
        rows = await list_outcomes_for_reaction(db, reaction_id)
    assert len(rows) == 1
    row = rows[0]
    assert row["source"] == "manual"
    assert row["status"] == "success"
    assert row["yield_pct"] == 72.5
    assert row["conditions_actual"] == {"solvent": "toluene", "temp_c": 80}
    assert row["observations"] == "Clean conversion"


@pytest.mark.asyncio
async def test_insert_outcome_eln_is_idempotent(session_factory, user_id: str) -> None:
    """Re-ingesting the same ELN experiment id must not duplicate."""
    reaction_id = await _insert_reaction(
        session_factory, user_id, rxn_smiles="CC>>CCBr",
    )

    eln_exp_id = f"EXP-{uuid.uuid4().hex[:10]}"
    async with session_factory() as db:
        async with db.begin():
            first_id, first_already = await insert_outcome(
                db,
                reaction_id=reaction_id,
                source="eln",
                status="partial",
                created_by=user_id,
                eln_experiment_id=eln_exp_id,
                yield_pct=42.0,
            )
    async with session_factory() as db:
        async with db.begin():
            second_id, second_already = await insert_outcome(
                db,
                reaction_id=reaction_id,
                source="eln",
                status="partial",
                created_by=user_id,
                eln_experiment_id=eln_exp_id,
                yield_pct=42.0,
            )

    assert first_already is False
    assert second_already is True
    assert first_id == second_id

    async with session_factory() as db:
        rows = await list_outcomes_for_reaction(db, reaction_id)
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_insert_outcome_rejects_bad_inputs(session_factory, user_id: str) -> None:
    reaction_id = await _insert_reaction(
        session_factory, user_id, rxn_smiles="A>>B",
    )

    async with session_factory() as db:
        with pytest.raises(ValueError, match="source"):
            await insert_outcome(
                db, reaction_id=reaction_id,
                source="bogus", status="success", created_by=user_id,
            )

    async with session_factory() as db:
        with pytest.raises(ValueError, match="status"):
            await insert_outcome(
                db, reaction_id=reaction_id,
                source="manual", status="bogus", created_by=user_id,
            )

    async with session_factory() as db:
        with pytest.raises(ValueError, match="yield_pct"):
            await insert_outcome(
                db, reaction_id=reaction_id,
                source="manual", status="success", created_by=user_id,
                yield_pct=150.0,
            )


@pytest.mark.asyncio
async def test_find_similar_reactions_includes_outcomes(session_factory, user_id: str) -> None:
    """When include_outcomes=True the returned hits carry their outcome list."""
    rid_a = await _insert_reaction(
        session_factory, user_id, rxn_smiles="CC>>CCO",
        name="rxn_a", drfp_bits=_FP_ALL_ONES,
    )
    rid_b = await _insert_reaction(
        session_factory, user_id, rxn_smiles="CC>>CCBr",
        name="rxn_b", drfp_bits=_FP_MOSTLY_ONES,
    )

    async with session_factory() as db:
        async with db.begin():
            await insert_outcome(
                db, reaction_id=rid_a, source="manual", status="success",
                created_by=user_id, yield_pct=85.0,
            )
            await insert_outcome(
                db, reaction_id=rid_a, source="manual", status="fail",
                created_by=user_id, failure_reason="Solvent inversion",
            )
            await insert_outcome(
                db, reaction_id=rid_b, source="eln", status="partial",
                created_by=user_id, eln_experiment_id=f"E-{uuid.uuid4().hex[:8]}",
                yield_pct=45.0,
            )

    async with session_factory() as db:
        with_out = await find_similar_reactions(
            db, _FP_ALL_ONES, limit=20, min_similarity=0.0,
            include_outcomes=True,
        )
        without_out = await find_similar_reactions(
            db, _FP_ALL_ONES, limit=20, min_similarity=0.0,
        )

    # Without outcomes the legacy shape is preserved (no `outcomes` key).
    for row in without_out:
        assert "outcomes" not in row

    by_id = {r["id"]: r for r in with_out}
    assert rid_a in by_id, "rxn_a should appear in the similar-reactions hit list"
    assert rid_b in by_id, "rxn_b should appear in the similar-reactions hit list"

    a_outcomes = by_id[rid_a]["outcomes"]
    assert len(a_outcomes) == 2
    # Newest first — both rows landed in this session, so just check the set.
    assert {o["status"] for o in a_outcomes} == {"success", "fail"}

    b_outcomes = by_id[rid_b]["outcomes"]
    assert len(b_outcomes) == 1
    assert b_outcomes[0]["source"] == "eln"
    assert b_outcomes[0]["yield_pct"] == 45.0
