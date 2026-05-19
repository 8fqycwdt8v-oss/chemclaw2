"""Cross-module owner-scoping smoke tests.

Lock in the CLAUDE.md rule: "Owner-scope every per-user write. Every
UPDATE/DELETE against per-user rows includes `user_id = :uid` in WHERE."

These tests stand up minimal fixtures, attempt the documented mutation
from a non-owner identity, and assert the row is untouched. They are
broader than test_wiki_queries.py (which round-trips one user) and
deliberately small per case — the goal is regression coverage if a
future refactor strips the user_id predicate.
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from api.db.queries.notifications import create_notification, mark_read
from api.db.queries.subscriptions import unsubscribe, subscribe
from api.db.queries.todos import upsert_todos, mark_todo_done


# ── notifications ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mark_read_owner_scoped(session_factory):
    owner = f"o-{uuid.uuid4().hex[:8]}"
    attacker = f"a-{uuid.uuid4().hex[:8]}"

    async with session_factory() as db:
        async with db.begin():
            n_id = await create_notification(db, owner, "test", {"x": 1})

    # Attacker tries to mark the owner's notification as read.
    async with session_factory() as db:
        updated = await mark_read(db, attacker, [n_id])
    assert updated == 0

    # Confirm the row is still unread.
    async with session_factory() as db:
        row = await db.execute(
            text("SELECT read FROM notifications WHERE id = CAST(:id AS uuid)"),
            {"id": n_id},
        )
        assert row.scalar_one() is False

    # Owner can still mark it read.
    async with session_factory() as db:
        updated = await mark_read(db, owner, [n_id])
    assert updated == 1


# ── todos ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_mark_todo_done_owner_scoped(session_factory):
    owner = f"o-{uuid.uuid4().hex[:8]}"
    attacker = f"a-{uuid.uuid4().hex[:8]}"
    sid = f"s-{uuid.uuid4().hex[:8]}"

    async with session_factory() as db:
        await upsert_todos(
            db, sid, owner,
            [{"text": "do thing", "status": "pending", "position": 0}],
        )
    async with session_factory() as db:
        row = await db.execute(
            text("SELECT id::text FROM agent_todos WHERE session_id = :sid AND user_id = :uid"),
            {"sid": sid, "uid": owner},
        )
        todo_id = row.scalar_one()

    async with session_factory() as db:
        ok = await mark_todo_done(db, todo_id, attacker)
    assert ok is False

    async with session_factory() as db:
        row = await db.execute(
            text("SELECT status FROM agent_todos WHERE id = CAST(:id AS uuid)"),
            {"id": todo_id},
        )
        assert row.scalar_one() == "pending"


# ── subscriptions ───────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_unsubscribe_owner_scoped(session_factory, wiki_page):
    owner = f"o-{uuid.uuid4().hex[:8]}"
    attacker = f"a-{uuid.uuid4().hex[:8]}"
    page_id = wiki_page["id"]

    async with session_factory() as db:
        await subscribe(db, owner, page_id)

    async with session_factory() as db:
        removed = await unsubscribe(db, attacker, page_id)
    assert removed is False

    async with session_factory() as db:
        row = await db.execute(
            text(
                "SELECT 1 FROM wiki_subscriptions WHERE user_id = :uid AND page_id = CAST(:pid AS uuid)"
            ),
            {"uid": owner, "pid": page_id},
        )
        assert row.one_or_none() is not None

    async with session_factory() as db:
        removed = await unsubscribe(db, owner, page_id)
    assert removed is True
