"""DB round-trip tests for the drive_sync_state queries.

Runs against the CI Postgres container (migration 0044 applied before pytest).
"""
from __future__ import annotations

import uuid

import pytest

from api.db.queries.drive_sync import get_sync_state, save_sync_state


@pytest.mark.asyncio
async def test_get_sync_state_absent_returns_none(session_factory) -> None:
    async with session_factory() as db:
        assert await get_sync_state(db, f"drive-{uuid.uuid4().hex}") is None


@pytest.mark.asyncio
async def test_save_then_get_roundtrip(session_factory) -> None:
    drive_id = f"drive-{uuid.uuid4().hex}"
    async with session_factory() as db:
        await save_sync_state(db, drive_id, status="ok", delta_token="TOKEN-1")
    async with session_factory() as db:
        state = await get_sync_state(db, drive_id)
    assert state is not None
    assert state["delta_token"] == "TOKEN-1"
    assert state["last_status"] == "ok"
    assert state["last_error"] is None
    assert state["last_synced_at"] is not None


@pytest.mark.asyncio
async def test_error_save_preserves_prior_delta_token(session_factory) -> None:
    """An error pass (delta_token=None) records the failure but keeps the
    previous cursor so the next run retries from the same point."""
    drive_id = f"drive-{uuid.uuid4().hex}"
    async with session_factory() as db:
        await save_sync_state(db, drive_id, status="ok", delta_token="TOKEN-1")
    async with session_factory() as db:
        await save_sync_state(
            db, drive_id, status="error", delta_token=None, error="graph 500"
        )
    async with session_factory() as db:
        state = await get_sync_state(db, drive_id)
    assert state is not None
    assert state["delta_token"] == "TOKEN-1"  # preserved via COALESCE
    assert state["last_status"] == "error"
    assert state["last_error"] == "graph 500"


@pytest.mark.asyncio
async def test_save_advances_delta_token_on_success(session_factory) -> None:
    drive_id = f"drive-{uuid.uuid4().hex}"
    async with session_factory() as db:
        await save_sync_state(db, drive_id, status="ok", delta_token="TOKEN-1")
    async with session_factory() as db:
        await save_sync_state(db, drive_id, status="ok", delta_token="TOKEN-2")
    async with session_factory() as db:
        state = await get_sync_state(db, drive_id)
    assert state is not None
    assert state["delta_token"] == "TOKEN-2"
