"""Tier D — batched campaign step queries (B1 from BACKLOG.md)."""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from api.db.queries.campaigns import (
    all_complete_for_campaigns,
    get_pending_steps_for_campaigns,
)


async def _make_campaign(session_factory, user_id: str, target: str = "CCO") -> str:
    async with session_factory() as db:
        async with db.begin():
            result = await db.execute(
                text("""
                    INSERT INTO synthesis_campaigns (created_by, session_id, target_smiles, status)
                    VALUES (:uid, :sid, :target, 'running')
                    RETURNING id::text
                """),
                {
                    "uid": user_id,
                    "sid": f"sess-{uuid.uuid4().hex[:12]}",
                    "target": target,
                },
            )
            return result.scalar_one()


async def _add_steps(
    session_factory, campaign_id: str, n: int,
    status: str = "pending", start_idx: int = 0,
):
    """Insert `n` campaign steps starting at `start_idx`. Use start_idx to
    extend a campaign that already has steps without colliding on the
    UNIQUE(campaign_id, step_idx) constraint from migration 0031."""
    async with session_factory() as db:
        async with db.begin():
            for i in range(n):
                idx = start_idx + i
                await db.execute(
                    text("""
                        INSERT INTO campaign_steps
                            (campaign_id, step_idx, reaction_smiles, conditions, status)
                        VALUES (CAST(:cid AS uuid), :idx, :smi, :cond, :st)
                    """),
                    {
                        "cid": campaign_id, "idx": idx,
                        "smi": f"reactant.{idx}>>product.{idx}",
                        "cond": f"step-{idx}",
                        "st": status,
                    },
                )


@pytest.mark.asyncio
async def test_batched_pending_steps_empty(session_factory):
    async with session_factory() as db:
        out = await get_pending_steps_for_campaigns(db, [])
    assert out == {}


@pytest.mark.asyncio
async def test_batched_pending_steps_groups_by_campaign(session_factory, user_id):
    c1 = await _make_campaign(session_factory, user_id, target="A")
    c2 = await _make_campaign(session_factory, user_id, target="B")
    await _add_steps(session_factory, c1, n=3)
    await _add_steps(session_factory, c2, n=2)

    async with session_factory() as db:
        out = await get_pending_steps_for_campaigns(db, [c1, c2])

    assert set(out.keys()) >= {c1, c2}
    assert len(out[c1]) == 3
    assert len(out[c2]) == 2
    assert [s["step_idx"] for s in out[c1]] == [0, 1, 2]


@pytest.mark.asyncio
async def test_all_complete_for_campaigns(session_factory, user_id):
    finished = await _make_campaign(session_factory, user_id, target="done")
    partial = await _make_campaign(session_factory, user_id, target="midway")
    empty = await _make_campaign(session_factory, user_id, target="empty")

    await _add_steps(session_factory, finished, n=2, status="complete")
    await _add_steps(session_factory, partial, n=1, status="complete", start_idx=0)
    await _add_steps(session_factory, partial, n=1, status="pending", start_idx=1)

    async with session_factory() as db:
        out = await all_complete_for_campaigns(db, [finished, partial, empty])

    assert out[finished] is True
    assert out[partial] is False
    assert out[empty] is False  # no steps → not complete by contract
