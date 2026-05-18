"""Tier D — budget atomic-consume tests (B2 from BACKLOG.md)."""
from __future__ import annotations

import uuid

import pytest

from api.db.queries.budgets import (
    try_consume_tool_call,
    upsert_project_budget,
    delete_project_budget,
)


@pytest.fixture
def project_key():
    return f"chemclaw2:test-{uuid.uuid4().hex[:8]}"


@pytest.mark.asyncio
async def test_no_budget_returns_none(session_factory, project_key):
    """When no budget is configured for the project, the function must
    return None so the caller treats it as 'unlimited'."""
    async with session_factory() as s:
        result = await try_consume_tool_call(s, project_key)
    assert result is None


@pytest.mark.asyncio
async def test_consume_under_cap(session_factory, project_key, user_id):
    async with session_factory() as s:
        await upsert_project_budget(
            s, project_key, period="day", tool_calls_cap=3,
            experiments_cap=None, tokens_cap=None, updated_by=user_id,
        )
    try:
        async with session_factory() as s:
            r1 = await try_consume_tool_call(s, project_key)
        assert r1 == {"ok": True, "used": 1, "cap": 3}
        async with session_factory() as s:
            r2 = await try_consume_tool_call(s, project_key)
        assert r2 == {"ok": True, "used": 2, "cap": 3}
        async with session_factory() as s:
            r3 = await try_consume_tool_call(s, project_key)
        assert r3 == {"ok": True, "used": 3, "cap": 3}
        async with session_factory() as s:
            r4 = await try_consume_tool_call(s, project_key)
        assert r4 == {"ok": False, "used": 3, "cap": 3}
    finally:
        async with session_factory() as s:
            await delete_project_budget(s, project_key)


@pytest.mark.asyncio
async def test_consume_zero_cap_always_blocks(session_factory, project_key, user_id):
    async with session_factory() as s:
        await upsert_project_budget(
            s, project_key, period="day", tool_calls_cap=0,
            experiments_cap=None, tokens_cap=None, updated_by=user_id,
        )
    try:
        async with session_factory() as s:
            r = await try_consume_tool_call(s, project_key)
        assert r is not None
        assert r["ok"] is False
        assert r["cap"] == 0
    finally:
        async with session_factory() as s:
            await delete_project_budget(s, project_key)


@pytest.mark.asyncio
async def test_consume_null_cap_unlimited(session_factory, project_key, user_id):
    async with session_factory() as s:
        await upsert_project_budget(
            s, project_key, period="day", tool_calls_cap=None,
            experiments_cap=None, tokens_cap=None, updated_by=user_id,
        )
    try:
        async with session_factory() as s:
            r1 = await try_consume_tool_call(s, project_key)
        assert r1 is not None and r1["ok"] is True and r1["cap"] is None
        async with session_factory() as s:
            r2 = await try_consume_tool_call(s, project_key)
        assert r2 is not None and r2["ok"] is True and r2["used"] == 2
    finally:
        async with session_factory() as s:
            await delete_project_budget(s, project_key)
