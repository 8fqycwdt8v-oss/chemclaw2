"""Tier D — batched campaign step queries (B1 from BACKLOG.md)."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from api.db.queries.campaigns import (
    all_complete_for_campaigns,
    get_pending_steps_for_campaigns,
)


async def _make_campaign(db, user_id, target="CCO"):
    async with db.begin():
        result = await db.execute(
            text("""
                INSERT INTO synthesis_campaigns (created_by, target_smiles, status)
                VALUES (:uid, :target, 'running')
                RETURNING id::text
            """),
            {"uid": user_id, "target": target},
        )
        return result.scalar_one()


async def _add_steps(db, campaign_id, n: int, status: str = "pending"):
    async with db.begin():
        for i in range(n):
            await db.execute(
                text("""
                    INSERT INTO campaign_steps
                        (campaign_id, step_idx, reaction_smiles, conditions, status)
                    VALUES (:cid::uuid, :idx, :smi, :cond, :st)
                """),
                {
                    "cid": campaign_id, "idx": i,
                    "smi": f"reactant.{i}>>product.{i}",
                    "cond": f"step-{i}",
                    "st": status,
                },
            )


@pytest.mark.asyncio
async def test_batched_pending_steps_empty(db):
    out = await get_pending_steps_for_campaigns(db, [])
    assert out == {}


@pytest.mark.asyncio
async def test_batched_pending_steps_groups_by_campaign(db, user_id):
    c1 = await _make_campaign(db, user_id, target="A")
    c2 = await _make_campaign(db, user_id, target="B")
    await _add_steps(db, c1, n=3)
    await _add_steps(db, c2, n=2)

    out = await get_pending_steps_for_campaigns(db, [c1, c2])
    assert set(out.keys()) >= {c1, c2}
    assert len(out[c1]) == 3
    assert len(out[c2]) == 2
    # Ordered by step_idx within each campaign.
    assert [s["step_idx"] for s in out[c1]] == [0, 1, 2]


@pytest.mark.asyncio
async def test_all_complete_for_campaigns(db, user_id):
    finished = await _make_campaign(db, user_id, target="done")
    partial = await _make_campaign(db, user_id, target="midway")
    empty = await _make_campaign(db, user_id, target="empty")

    await _add_steps(db, finished, n=2, status="complete")
    await _add_steps(db, partial, n=1, status="complete")
    await _add_steps(db, partial, n=1, status="pending")
    # empty: no steps

    out = await all_complete_for_campaigns(db, [finished, partial, empty])
    assert out[finished] is True
    assert out[partial] is False
    # No steps → not "complete" by the implementation's contract.
    assert out[empty] is False
