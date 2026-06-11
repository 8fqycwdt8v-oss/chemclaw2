"""End-to-end fingerprint pipeline test: real DB + real MCP subprocess.

Every layer of this pipeline was individually mocked elsewhere, which let
four stacked production bugs hide for months: the servers crashed at startup
(`extra={"name": ...}` KeyError), the worker skipped the MCP initialize
handshake, it passed `rxn_smiles` to a tool whose parameter is
`reaction_smiles`, and the compound write set a GENERATED ALWAYS column.
This test runs the whole chain unmocked — DB poll → subprocess spawn →
handshake → RDKit/DRFP compute → idempotent write — and would have caught
all four.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

pytest.importorskip("rdkit")
pytest.importorskip("mcp_molfp")
pytest.importorskip("mcp_rxnfp")

from api.workers.fp_worker import (  # noqa: E402
    compute_compound_fingerprints,
    compute_reaction_fingerprints,
)


@pytest.mark.asyncio
async def test_compound_fp_pipeline_end_to_end(session_factory) -> None:
    cid = str(uuid.uuid4())
    async with session_factory() as db:
        await db.execute(
            text("INSERT INTO compounds (id, smiles, created_by) VALUES (CAST(:id AS uuid), :smi, 'fp-e2e')"),
            {"id": cid, "smi": "c1ccc2c(c1)cccc2O"},
        )
        await db.commit()
    try:
        async with session_factory() as db:
            computed = await compute_compound_fingerprints(db)
        assert computed >= 1
        async with session_factory() as db:
            row = (
                await db.execute(
                    text(
                        "SELECT morgan_fp IS NOT NULL AS has_fp, morgan_fp_popcount "
                        "FROM compounds WHERE id = CAST(:id AS uuid)"
                    ),
                    {"id": cid},
                )
            ).one()
        assert row.has_fp, "morgan_fp must be written by the worker"
        # GENERATED ALWAYS column derives from morgan_fp — nonzero proves both
        # the write and the generation fired.
        assert row.morgan_fp_popcount > 0
    finally:
        async with session_factory() as db:
            await db.execute(
                text("DELETE FROM compounds WHERE id = CAST(:id AS uuid)"), {"id": cid}
            )
            await db.commit()


@pytest.mark.asyncio
async def test_reaction_fp_pipeline_end_to_end(session_factory) -> None:
    rid = str(uuid.uuid4())
    async with session_factory() as db:
        await db.execute(
            text("INSERT INTO reactions (id, rxn_smiles, created_by) VALUES (CAST(:id AS uuid), :smi, 'fp-e2e')"),
            {"id": rid, "smi": "CCO.CC(=O)O>>CCOC(C)=O"},
        )
        await db.commit()
    try:
        async with session_factory() as db:
            computed = await compute_reaction_fingerprints(db)
        assert computed >= 1
        async with session_factory() as db:
            row = (
                await db.execute(
                    text("SELECT drfp IS NOT NULL AS has_fp FROM reactions WHERE id = CAST(:id AS uuid)"),
                    {"id": rid},
                )
            ).one()
        assert row.has_fp, "drfp must be written by the worker"
    finally:
        async with session_factory() as db:
            await db.execute(
                text("DELETE FROM reactions WHERE id = CAST(:id AS uuid)"), {"id": rid}
            )
            await db.commit()
