"""Integration tests for investigations / world model / hypotheses (Phase B).

Exercise the query layer against the CI Postgres so owner-scoping,
transactions, and the Elo eager-update behaviour are validated end-to-end.

Skipped automatically when DATABASE_URL points at an unreachable Postgres
(local dev without `make db.up`).
"""
from __future__ import annotations

import uuid

import pytest

from api.db.queries.hypotheses import (
    create_hypothesis,
    get_hypothesis,
    list_hypotheses,
    list_recent_rankings,
    record_pairwise_ranking,
    retire_hypothesis,
)
from api.db.queries.investigations import (
    create_investigation,
    get_investigation,
    list_investigations,
    update_investigation_status,
)
from api.db.queries.world_model import (
    add_world_model_entry,
    list_world_model_entries,
    search_world_model_entries,
    update_world_model_entry_status,
)


# ── investigations ───────────────────────────────────────────────────────────


async def test_create_and_get_investigation(session_factory, user_id: str) -> None:
    async with session_factory() as db:
        iid = await create_investigation(
            db, "JAK1 selectivity", "Find a JAK1-selective lead", user_id,
        )
    async with session_factory() as db:
        row = await get_investigation(db, iid, user_id)
    assert row is not None
    assert row["title"] == "JAK1 selectivity"
    assert row["objective"] == "Find a JAK1-selective lead"
    assert row["status"] == "active"
    assert row["created_by"] == user_id


async def test_investigation_owner_scoped(session_factory, user_id: str) -> None:
    other_user = f"u-{uuid.uuid4().hex[:8]}"
    async with session_factory() as db:
        iid = await create_investigation(db, "T1", "Objective", user_id)
    async with session_factory() as db:
        row = await get_investigation(db, iid, other_user)
    assert row is None, "stranger should not see another user's investigation"


async def test_list_investigations_filters_by_status(
    session_factory, user_id: str,
) -> None:
    async with session_factory() as db:
        active = await create_investigation(db, "A", "obj", user_id)
        paused = await create_investigation(db, "B", "obj", user_id)
    async with session_factory() as db:
        await update_investigation_status(db, paused, user_id, "paused")
    async with session_factory() as db:
        all_rows = await list_investigations(db, user_id)
        active_rows = await list_investigations(db, user_id, status="active")
        paused_rows = await list_investigations(db, user_id, status="paused")
    all_ids = {r["id"] for r in all_rows}
    assert {active, paused} <= all_ids
    assert active in {r["id"] for r in active_rows}
    assert paused not in {r["id"] for r in active_rows}
    assert paused in {r["id"] for r in paused_rows}


async def test_update_investigation_status_rejects_invalid(
    session_factory, user_id: str,
) -> None:
    async with session_factory() as db:
        iid = await create_investigation(db, "T", "o", user_id)
    async with session_factory() as db:
        with pytest.raises(ValueError, match="status must be one of"):
            await update_investigation_status(db, iid, user_id, "bogus")


async def test_update_investigation_status_owner_scoped(
    session_factory, user_id: str,
) -> None:
    other = f"u-{uuid.uuid4().hex[:8]}"
    async with session_factory() as db:
        iid = await create_investigation(db, "T", "o", user_id)
    async with session_factory() as db:
        ok = await update_investigation_status(db, iid, other, "paused")
    assert ok is False, "stranger's update must fail closed (no rows affected)"


# ── world model ──────────────────────────────────────────────────────────────


async def test_add_and_query_world_model_entries(
    session_factory, user_id: str,
) -> None:
    async with session_factory() as db:
        iid = await create_investigation(db, "T", "o", user_id)
        await add_world_model_entry(
            db, iid, user_id, "fact",
            "JAK1 is a tyrosine kinase in the JAK family.", confidence=0.95,
        )
        await add_world_model_entry(
            db, iid, user_id, "open_question",
            "What's the off-target liability of compound X?",
        )
    async with session_factory() as db:
        all_entries = await list_world_model_entries(db, iid, user_id)
        facts = await list_world_model_entries(db, iid, user_id, kind="fact")
        questions = await list_world_model_entries(
            db, iid, user_id, kind="open_question",
        )
    assert len(all_entries) == 2
    assert len(facts) == 1 and facts[0]["kind"] == "fact"
    assert facts[0]["confidence"] == pytest.approx(0.95)
    assert len(questions) == 1 and questions[0]["kind"] == "open_question"


async def test_add_world_model_entry_rejects_bad_kind_and_confidence(
    session_factory, user_id: str,
) -> None:
    async with session_factory() as db:
        iid = await create_investigation(db, "T", "o", user_id)
    async with session_factory() as db:
        with pytest.raises(ValueError, match="kind must be"):
            await add_world_model_entry(db, iid, user_id, "rumour", "x")
    async with session_factory() as db:
        with pytest.raises(ValueError, match="confidence must be"):
            await add_world_model_entry(
                db, iid, user_id, "fact", "x", confidence=1.5,
            )


async def test_supersede_world_model_entry(session_factory, user_id: str) -> None:
    async with session_factory() as db:
        iid = await create_investigation(db, "T", "o", user_id)
        eid = await add_world_model_entry(
            db, iid, user_id, "assumption", "Compound X is bioavailable.",
        )
    async with session_factory() as db:
        ok = await update_world_model_entry_status(
            db, eid, user_id, "superseded",
        )
    assert ok is True
    async with session_factory() as db:
        rows = await list_world_model_entries(
            db, iid, user_id, status="superseded",
        )
    assert len(rows) == 1
    assert rows[0]["id"] == eid


async def test_supersede_world_model_entry_owner_scoped(
    session_factory, user_id: str,
) -> None:
    other = f"u-{uuid.uuid4().hex[:8]}"
    async with session_factory() as db:
        iid = await create_investigation(db, "T", "o", user_id)
        eid = await add_world_model_entry(db, iid, user_id, "fact", "x")
    async with session_factory() as db:
        ok = await update_world_model_entry_status(db, eid, other, "closed")
    assert ok is False


async def test_search_world_model_entries(session_factory, user_id: str) -> None:
    async with session_factory() as db:
        iid = await create_investigation(db, "T", "o", user_id)
        await add_world_model_entry(
            db, iid, user_id, "fact",
            "ROCK inhibition enhances RPE phagocytosis.",
        )
        await add_world_model_entry(
            db, iid, user_id, "fact",
            "Compound libraries should screen against off-targets.",
        )
    async with session_factory() as db:
        hits = await search_world_model_entries(
            db, iid, user_id, "phagocytosis",
        )
    assert len(hits) >= 1
    assert any("phagocytosis" in r["content"].lower() for r in hits)


# ── hypotheses ───────────────────────────────────────────────────────────────


async def test_create_and_list_hypotheses(session_factory, user_id: str) -> None:
    async with session_factory() as db:
        iid = await create_investigation(db, "T", "o", user_id)
        a = await create_hypothesis(db, iid, user_id, "A is better")
        b = await create_hypothesis(db, iid, user_id, "B is better")
    async with session_factory() as db:
        rows = await list_hypotheses(db, iid, user_id)
    ids = {r["id"] for r in rows}
    assert {a, b} <= ids
    for r in rows:
        # Default elo is 1000 until first ranking.
        assert r["elo_rating"] == pytest.approx(1000.0)
        assert r["status"] == "proposed"


async def test_create_hypothesis_with_evolution_parent(
    session_factory, user_id: str,
) -> None:
    async with session_factory() as db:
        iid = await create_investigation(db, "T", "o", user_id)
        parent = await create_hypothesis(db, iid, user_id, "Parent claim")
        child = await create_hypothesis(
            db, iid, user_id, "Refined child claim", parent_id=parent,
        )
    async with session_factory() as db:
        row = await get_hypothesis(db, child, user_id)
    assert row is not None
    assert row["parent_id"] == parent


async def test_create_hypothesis_rejects_unowned_parent(
    session_factory, user_id: str,
) -> None:
    other = f"u-{uuid.uuid4().hex[:8]}"
    async with session_factory() as db:
        iid_other = await create_investigation(db, "T", "o", other)
        their_parent = await create_hypothesis(
            db, iid_other, other, "their hypothesis",
        )
        my_iid = await create_investigation(db, "T2", "o", user_id)
    async with session_factory() as db:
        with pytest.raises(ValueError, match="parent_id not found"):
            await create_hypothesis(
                db, my_iid, user_id, "child", parent_id=their_parent,
            )


async def test_pairwise_ranking_updates_elo_eagerly(
    session_factory, user_id: str,
) -> None:
    async with session_factory() as db:
        iid = await create_investigation(db, "T", "o", user_id)
        a = await create_hypothesis(db, iid, user_id, "A")
        b = await create_hypothesis(db, iid, user_id, "B")
    async with session_factory() as db:
        result = await record_pairwise_ranking(
            db, iid, user_id, a, b, "a", "A is stronger", user_id,
        )
    # a's new rating > 1000, b's < 1000, and they're symmetric around 1000
    assert result["hypothesis_a"]["new_elo_rating"] > 1000
    assert result["hypothesis_b"]["new_elo_rating"] < 1000
    assert (result["hypothesis_a"]["new_elo_rating"] - 1000) == pytest.approx(
        1000 - result["hypothesis_b"]["new_elo_rating"], abs=1e-6,
    )
    # Stored values match returned values.
    async with session_factory() as db:
        a_row = await get_hypothesis(db, a, user_id)
        b_row = await get_hypothesis(db, b, user_id)
    assert a_row is not None and b_row is not None
    assert a_row["elo_rating"] == pytest.approx(
        result["hypothesis_a"]["new_elo_rating"],
    )
    assert b_row["elo_rating"] == pytest.approx(
        result["hypothesis_b"]["new_elo_rating"],
    )
    # First ranking moves both out of 'proposed'.
    assert a_row["status"] == "ranked"
    assert b_row["status"] == "ranked"


async def test_pairwise_ranking_rejects_self_pair(
    session_factory, user_id: str,
) -> None:
    async with session_factory() as db:
        iid = await create_investigation(db, "T", "o", user_id)
        a = await create_hypothesis(db, iid, user_id, "A")
    async with session_factory() as db:
        with pytest.raises(ValueError, match="against itself"):
            await record_pairwise_ranking(
                db, iid, user_id, a, a, "a", None, user_id,
            )


async def test_pairwise_ranking_rejects_cross_investigation(
    session_factory, user_id: str,
) -> None:
    async with session_factory() as db:
        iid1 = await create_investigation(db, "T1", "o", user_id)
        iid2 = await create_investigation(db, "T2", "o", user_id)
        a = await create_hypothesis(db, iid1, user_id, "A")
        b = await create_hypothesis(db, iid2, user_id, "B")
    async with session_factory() as db:
        with pytest.raises(ValueError, match="different investigation"):
            await record_pairwise_ranking(
                db, iid1, user_id, a, b, "a", None, user_id,
            )


async def test_pairwise_ranking_owner_scoped(
    session_factory, user_id: str,
) -> None:
    other = f"u-{uuid.uuid4().hex[:8]}"
    async with session_factory() as db:
        iid_other = await create_investigation(db, "T", "o", other)
        a = await create_hypothesis(db, iid_other, other, "A")
        b = await create_hypothesis(db, iid_other, other, "B")
    async with session_factory() as db:
        with pytest.raises(ValueError, match="not found or not owned"):
            await record_pairwise_ranking(
                db, iid_other, user_id, a, b, "a", None, user_id,
            )


async def test_recent_rankings_logged(session_factory, user_id: str) -> None:
    async with session_factory() as db:
        iid = await create_investigation(db, "T", "o", user_id)
        a = await create_hypothesis(db, iid, user_id, "A")
        b = await create_hypothesis(db, iid, user_id, "B")
        await record_pairwise_ranking(
            db, iid, user_id, a, b, "a", "reason", user_id,
        )
        await record_pairwise_ranking(
            db, iid, user_id, a, b, "tie", "another round", user_id,
        )
    async with session_factory() as db:
        rows = await list_recent_rankings(db, iid, user_id)
    assert len(rows) == 2
    # Newest first.
    assert rows[0]["winner"] == "tie"
    assert rows[1]["winner"] == "a"


async def test_retire_hypothesis_is_idempotent(
    session_factory, user_id: str,
) -> None:
    async with session_factory() as db:
        iid = await create_investigation(db, "T", "o", user_id)
        h = await create_hypothesis(db, iid, user_id, "Doomed claim")
    async with session_factory() as db:
        first = await retire_hypothesis(db, h, user_id)
        second = await retire_hypothesis(db, h, user_id)
    assert first is True
    assert second is False, "second retire is a no-op (source-state predicate)"
