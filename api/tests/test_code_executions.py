"""Integration tests for the code_executions audit log."""
from __future__ import annotations

import uuid

import pytest

from api.db.queries.code_executions import (
    get_execution,
    insert_execution,
    list_executions,
)


async def test_insert_and_list_session_scoped(
    session_factory, user_id: str,
) -> None:
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    async with session_factory() as db:
        eid = await insert_execution(
            db,
            code="print('hi')",
            stdout="hi\n",
            stderr="",
            exit_code=0,
            duration_ms=42,
            status="completed",
            created_by=user_id,
            session_id=sid,
        )
    async with session_factory() as db:
        rows = await list_executions(db, user_id, session_id=sid)
    assert len(rows) == 1
    assert rows[0]["id"] == eid
    assert rows[0]["session_id"] == sid
    assert rows[0]["status"] == "completed"


async def test_insert_requires_anchor(session_factory, user_id: str) -> None:
    """Either investigation_id or session_id must be set (DB CHECK)."""
    async with session_factory() as db:
        with pytest.raises(ValueError, match="at least one"):
            await insert_execution(
                db, code="x", stdout="", stderr="", exit_code=0,
                duration_ms=0, status="completed", created_by=user_id,
            )


async def test_get_execution_owner_scoped(session_factory, user_id: str) -> None:
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    other = f"u-{uuid.uuid4().hex[:8]}"
    async with session_factory() as db:
        eid = await insert_execution(
            db, code="x", stdout="", stderr="", exit_code=0,
            duration_ms=0, status="completed", created_by=user_id,
            session_id=sid,
        )
    async with session_factory() as db:
        mine = await get_execution(db, eid, user_id)
        theirs = await get_execution(db, eid, other)
    assert mine is not None
    assert theirs is None


async def test_insert_rejects_unknown_status(
    session_factory, user_id: str,
) -> None:
    sid = f"sess-{uuid.uuid4().hex[:8]}"
    async with session_factory() as db:
        with pytest.raises(ValueError, match="status must be one of"):
            await insert_execution(
                db, code="x", stdout="", stderr="", exit_code=0,
                duration_ms=0, status="bogus", created_by=user_id,
                session_id=sid,
            )
