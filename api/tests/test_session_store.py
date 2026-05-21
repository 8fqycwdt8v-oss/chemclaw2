"""Tests for PostgresSessionStore — the Claude Agent SDK SessionStore
implementation backed by `agent_sessions`.

The interesting paths are the advisory-lock append + the bi-temporal load.
This file specifically covers `delete()`, which has its own transaction
shape and was previously committing via bare `await db.commit()` instead
of `async with db.begin():` (CLAUDE.md: "wrap multi-step state
transitions in `async with session.begin()`").
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from api.db.queries.session_store import PostgresSessionStore


@pytest.mark.asyncio
async def test_delete_removes_only_matching_session(session_factory) -> None:
    """delete(key) must drop every row matching project_key+session_id
    (and subpath if specified), and nothing else."""
    store = PostgresSessionStore(session_factory)
    project = "test-proj"
    sid_a = "sess-delete-a"
    sid_b = "sess-delete-b"

    await store.append({"project_key": project, "session_id": sid_a, "subpath": ""}, [{"x": 1}])
    await store.append({"project_key": project, "session_id": sid_a, "subpath": "sub"}, [{"y": 2}])
    await store.append({"project_key": project, "session_id": sid_b, "subpath": ""}, [{"z": 3}])

    # Subpath-less delete (`subpath=""` triggers the wildcard branch in
    # the WHERE clause) drops every row for sid_a, leaves sid_b alone.
    await store.delete({"project_key": project, "session_id": sid_a, "subpath": ""})

    async with session_factory() as db:
        result = await db.execute(text("""
            SELECT session_id, subpath FROM agent_sessions
            WHERE project_key = :p AND session_id IN (:a, :b)
            ORDER BY session_id, subpath
        """), {"p": project, "a": sid_a, "b": sid_b})
        remaining = [(r.session_id, r.subpath) for r in result]

    assert remaining == [(sid_b, "")]

    # Clean up.
    await store.delete({"project_key": project, "session_id": sid_b, "subpath": ""})


@pytest.mark.asyncio
async def test_delete_with_subpath_scoped_to_that_subpath(session_factory) -> None:
    """delete(key with non-empty subpath) must drop only that subpath row,
    not other subpaths of the same session."""
    store = PostgresSessionStore(session_factory)
    project = "test-proj"
    sid = "sess-subpath-delete"

    await store.append({"project_key": project, "session_id": sid, "subpath": ""}, [{"a": 1}])
    await store.append({"project_key": project, "session_id": sid, "subpath": "sub-1"}, [{"b": 2}])
    await store.append({"project_key": project, "session_id": sid, "subpath": "sub-2"}, [{"c": 3}])

    await store.delete({"project_key": project, "session_id": sid, "subpath": "sub-1"})

    async with session_factory() as db:
        result = await db.execute(text("""
            SELECT subpath FROM agent_sessions
            WHERE project_key = :p AND session_id = :sid
            ORDER BY subpath
        """), {"p": project, "sid": sid})
        remaining = sorted(r.subpath for r in result)

    assert remaining == ["", "sub-2"]

    # Clean up the rest.
    await store.delete({"project_key": project, "session_id": sid, "subpath": ""})
