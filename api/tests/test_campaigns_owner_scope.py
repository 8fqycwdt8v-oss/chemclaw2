"""Owner-scoping and state-machine tests for `api.db.queries.campaigns`.

These tests require a real Postgres (run in CI) and lock in two CLAUDE.md
rules:

  - "Owner-scope every per-user write" — a second user must NOT be able
    to UPDATE/DELETE rows owned by the first user via the documented query
    functions.
  - "Repeat the source-state predicate on every transition UPDATE" — a
    campaign that has already advanced to `complete` must NOT be advanced
    again, even if the caller passes a different status.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from api.db.queries.campaigns import (
    add_campaign_step,
    approve_step,
    cancel_campaign,
    reject_step,
    system_advance_campaign,
    update_campaign_status,
)


async def _new_campaign(session_factory, user_id: str, status: str = "planning") -> str:
    async with session_factory() as db:
        async with db.begin():
            result = await db.execute(
                text("""
                    INSERT INTO synthesis_campaigns (created_by, session_id, target_smiles, status)
                    VALUES (:uid, :sid, :target, :status)
                    RETURNING id::text
                """),
                {
                    "uid": user_id,
                    "sid": f"sess-{uuid.uuid4().hex[:12]}",
                    "target": "CCO",
                    "status": status,
                },
            )
            return result.scalar_one()


async def _status(session_factory, campaign_id: str) -> str:
    async with session_factory() as db:
        row = await db.execute(
            text("SELECT status FROM synthesis_campaigns WHERE id = CAST(:id AS uuid)"),
            {"id": campaign_id},
        )
        return row.scalar_one()


# ── Owner-scoping ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_campaign_status_denies_other_user(session_factory):
    """A user must not be able to advance another user's campaign."""
    owner = f"owner-{uuid.uuid4().hex[:8]}"
    attacker = f"attacker-{uuid.uuid4().hex[:8]}"
    cid = await _new_campaign(session_factory, owner, status="planning")

    async with session_factory() as db:
        async with db.begin():
            await update_campaign_status(db, cid, attacker, "running")

    assert await _status(session_factory, cid) == "planning"


@pytest.mark.asyncio
async def test_cancel_campaign_denies_other_user(session_factory):
    """cancel_campaign returns False and leaves the row untouched when the
    caller is not the owner."""
    owner = f"owner-{uuid.uuid4().hex[:8]}"
    attacker = f"attacker-{uuid.uuid4().hex[:8]}"
    cid = await _new_campaign(session_factory, owner, status="running")

    async with session_factory() as db:
        ok = await cancel_campaign(db, cid, attacker)

    assert ok is False
    assert await _status(session_factory, cid) == "running"


@pytest.mark.asyncio
async def test_cancel_campaign_owner_succeeds(session_factory):
    owner = f"owner-{uuid.uuid4().hex[:8]}"
    cid = await _new_campaign(session_factory, owner, status="running")

    async with session_factory() as db:
        ok = await cancel_campaign(db, cid, owner)

    assert ok is True
    assert await _status(session_factory, cid) == "failed"


# ── Transition predicates ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_update_campaign_status_rejects_terminal_source(session_factory):
    """Once a campaign is in a terminal status (`complete`/`failed`), the
    per-user update path must not transition it again. This is the
    'repeat source-state predicate' CLAUDE.md rule."""
    owner = f"owner-{uuid.uuid4().hex[:8]}"
    cid = await _new_campaign(session_factory, owner, status="complete")

    async with session_factory() as db:
        async with db.begin():
            await update_campaign_status(db, cid, owner, "running")

    assert await _status(session_factory, cid) == "complete"


@pytest.mark.asyncio
async def test_system_advance_rejects_terminal_source(session_factory):
    """Same rule applies to the system/worker path."""
    owner = f"owner-{uuid.uuid4().hex[:8]}"
    cid = await _new_campaign(session_factory, owner, status="failed")

    async with session_factory() as db:
        async with db.begin():
            await system_advance_campaign(db, cid, "running")

    assert await _status(session_factory, cid) == "failed"


@pytest.mark.asyncio
async def test_cancel_after_complete_is_noop(session_factory):
    """cancel_campaign on a terminal campaign returns False, leaves status alone."""
    owner = f"owner-{uuid.uuid4().hex[:8]}"
    cid = await _new_campaign(session_factory, owner, status="complete")

    async with session_factory() as db:
        ok = await cancel_campaign(db, cid, owner)

    assert ok is False
    assert await _status(session_factory, cid) == "complete"


# ── Step approval ────────────────────────────────────────────────────────────


async def _step_status(session_factory, campaign_id: str, step_idx: int) -> str:
    async with session_factory() as db:
        row = await db.execute(
            text(
                "SELECT status FROM campaign_steps "
                "WHERE campaign_id = CAST(:cid AS uuid) AND step_idx = :idx"
            ),
            {"cid": campaign_id, "idx": step_idx},
        )
        return row.scalar_one()


async def _add_step_awaiting_approval(
    session_factory, campaign_id: str, step_idx: int
) -> None:
    async with session_factory() as db:
        async with db.begin():
            await add_campaign_step(
                db,
                campaign_id,
                step_idx,
                "C>>C",
                "test conditions",
                status="pending_approval",
            )


@pytest.mark.asyncio
async def test_approve_step_owner_succeeds(session_factory):
    """The campaign owner promotes a step from pending_approval to pending."""
    owner = f"owner-{uuid.uuid4().hex[:8]}"
    cid = await _new_campaign(session_factory, owner, status="running")
    await _add_step_awaiting_approval(session_factory, cid, 0)

    async with session_factory() as db:
        ok = await approve_step(db, cid, 0, owner)

    assert ok is True
    assert await _step_status(session_factory, cid, 0) == "pending"


@pytest.mark.asyncio
async def test_approve_step_denies_other_user(session_factory):
    """A non-owner cannot promote another user's step."""
    owner = f"owner-{uuid.uuid4().hex[:8]}"
    attacker = f"attacker-{uuid.uuid4().hex[:8]}"
    cid = await _new_campaign(session_factory, owner, status="running")
    await _add_step_awaiting_approval(session_factory, cid, 0)

    async with session_factory() as db:
        ok = await approve_step(db, cid, 0, attacker)

    assert ok is False
    assert await _step_status(session_factory, cid, 0) == "pending_approval"


@pytest.mark.asyncio
async def test_approve_step_rejects_wrong_source_state(session_factory):
    """A step already in 'pending' (not 'pending_approval') cannot be re-approved."""
    owner = f"owner-{uuid.uuid4().hex[:8]}"
    cid = await _new_campaign(session_factory, owner, status="running")
    # Insert as 'pending' directly.
    async with session_factory() as db:
        async with db.begin():
            await add_campaign_step(db, cid, 0, "C>>C", "x", status="pending")

    async with session_factory() as db:
        ok = await approve_step(db, cid, 0, owner)

    # Source-state predicate fails — no update.
    assert ok is False
    assert await _step_status(session_factory, cid, 0) == "pending"


@pytest.mark.asyncio
async def test_reject_step_owner_succeeds(session_factory):
    owner = f"owner-{uuid.uuid4().hex[:8]}"
    cid = await _new_campaign(session_factory, owner, status="running")
    await _add_step_awaiting_approval(session_factory, cid, 0)

    async with session_factory() as db:
        ok = await reject_step(db, cid, 0, owner)

    assert ok is True
    assert await _step_status(session_factory, cid, 0) == "failed"


@pytest.mark.asyncio
async def test_reject_step_denies_other_user(session_factory):
    owner = f"owner-{uuid.uuid4().hex[:8]}"
    attacker = f"attacker-{uuid.uuid4().hex[:8]}"
    cid = await _new_campaign(session_factory, owner, status="running")
    await _add_step_awaiting_approval(session_factory, cid, 0)

    async with session_factory() as db:
        ok = await reject_step(db, cid, 0, attacker)

    assert ok is False
    assert await _step_status(session_factory, cid, 0) == "pending_approval"
