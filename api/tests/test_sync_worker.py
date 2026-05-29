"""Tests for the drive-sync worker.

`_sync_due` and content-type resolution are pure. `run_sync_once` is exercised
against the CI Postgres with the Graph calls and ingest mocked, so the test
covers the worker's orchestration (delta → file selection → ingest → cursor
persistence) without a live tenant or LLM/embedding calls.
"""
from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from api.db.queries.drive_sync import get_sync_state
from api.integrations.extractors import resolve_content_type
from api.workers import sync_worker as sw

# ── pure helpers ──────────────────────────────────────────────────────────────

def test_sync_due_never_synced() -> None:
    assert sw._sync_due(None, 12) is True


def test_sync_due_recent_is_not_due() -> None:
    recent = datetime.now(UTC) - timedelta(hours=1)
    assert sw._sync_due(recent, 12) is False


def test_sync_due_old_is_due() -> None:
    old = datetime.now(UTC) - timedelta(hours=13)
    assert sw._sync_due(old, 12) is True


def test_sync_due_naive_datetime_treated_as_utc() -> None:
    old_naive = datetime.utcnow() - timedelta(hours=13)
    assert sw._sync_due(old_naive, 12) is True


def test_resolve_content_type_trusts_known_declared() -> None:
    assert resolve_content_type("x.bin", "application/pdf") == "application/pdf"


def test_resolve_content_type_falls_back_to_extension() -> None:
    assert resolve_content_type("report.DOCX", "application/octet-stream") == (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    )
    assert resolve_content_type("notes.md", None) == "text/markdown"
    assert resolve_content_type("mystery.xyz", None) is None


# ── run_sync_once orchestration (DB + mocked Graph/ingest) ─────────────────────

@pytest.fixture
def graph_env(monkeypatch: pytest.MonkeyPatch) -> str:
    drive_id = f"drive-{uuid.uuid4().hex}"
    monkeypatch.setenv("MSGRAPH_TENANT_ID", "t")
    monkeypatch.setenv("MSGRAPH_CLIENT_ID", "c")
    monkeypatch.setenv("MSGRAPH_CLIENT_SECRET", "s")
    monkeypatch.setenv("MSGRAPH_DRIVE_ID", drive_id)
    return drive_id


@pytest.mark.asyncio
async def test_run_sync_once_not_configured(monkeypatch: pytest.MonkeyPatch, session_factory) -> None:
    for v in ("MSGRAPH_TENANT_ID", "MSGRAPH_CLIENT_ID", "MSGRAPH_CLIENT_SECRET", "MSGRAPH_DRIVE_ID"):
        monkeypatch.delenv(v, raising=False)
    result = await sw.run_sync_once(session_factory)
    assert result == {"status": "skipped", "reason": "not configured"}


@pytest.mark.asyncio
async def test_run_sync_once_ingests_and_saves_cursor(
    monkeypatch: pytest.MonkeyPatch, session_factory, graph_env
) -> None:
    drive_id = graph_env
    delta_items = [
        {"id": "1", "name": "a.pdf", "file": {"mimeType": "application/pdf"},
         "size": 100, "@microsoft.graph.downloadUrl": "https://x.sharepoint.com/a"},
        {"id": "2", "folder": {}, "name": "folder"},  # skipped (folder)
        {"id": "3", "name": "huge.txt", "file": {"mimeType": "text/plain"},
         "size": 99_000_000, "@microsoft.graph.downloadUrl": "https://x.sharepoint.com/h"},  # oversize
        {"id": "4", "name": "mystery.xyz", "file": {"mimeType": "application/octet-stream"},
         "size": 10, "@microsoft.graph.downloadUrl": "https://x.sharepoint.com/m"},  # unsupported type
    ]

    ingested_calls: list[dict[str, Any]] = []

    async def _fake_acquire(config):  # noqa: ANN001
        return "tok"

    async def _fake_delta(token, did, *, delta_link=None, timeout=30.0):  # noqa: ANN001
        return delta_items, "NEWCURSOR"

    async def _fake_download(url, *, timeout=60.0):  # noqa: ANN001
        return b"%PDF-1.7 body"

    async def _fake_ingest(db, **kwargs):  # noqa: ANN001
        ingested_calls.append(kwargs)
        return {"fact_id": "f", "kg": {"facts": 1, "hypotheses": 0}}

    monkeypatch.setattr(sw, "acquire_token", _fake_acquire)
    monkeypatch.setattr(sw, "delta", _fake_delta)
    monkeypatch.setattr(sw, "download_by_url", _fake_download)
    monkeypatch.setattr(sw, "ingest_document", _fake_ingest)

    result = await sw.run_sync_once(session_factory)

    assert result["status"] == "ok"
    assert result["ingested"] == 1       # only a.pdf
    assert result["skipped"] == 2        # huge.txt (oversize) + mystery.xyz (unsupported)
    assert result["files"] == 3          # 3 file items kept (folder dropped by select)
    # The one ingested file went through full + KG extraction into a corpus.
    assert len(ingested_calls) == 1
    call = ingested_calls[0]
    assert call["content_type"] == "application/pdf"
    assert call["extract"] == "full"
    assert call["extract_kg"] is True
    assert call["investigation_id"]

    # Cursor persisted for the next incremental run.
    async with session_factory() as db:
        state = await get_sync_state(db, drive_id)
    assert state is not None
    assert state["delta_token"] == "NEWCURSOR"
    assert state["last_status"] == "ok"


@pytest.mark.asyncio
async def test_run_sync_once_records_error_on_delta_failure(
    monkeypatch: pytest.MonkeyPatch, session_factory, graph_env
) -> None:
    drive_id = graph_env

    async def _fake_acquire(config):  # noqa: ANN001
        return "tok"

    async def _boom_delta(*a, **k):  # noqa: ANN001
        raise RuntimeError("graph 500")

    monkeypatch.setattr(sw, "acquire_token", _fake_acquire)
    monkeypatch.setattr(sw, "delta", _boom_delta)

    result = await sw.run_sync_once(session_factory)
    assert result == {"status": "error", "stage": "delta"}

    async with session_factory() as db:
        state = await get_sync_state(db, drive_id)
    assert state is not None
    assert state["last_status"] == "error"
    assert state["delta_token"] is None  # nothing to advance to


# ── admin trigger route ───────────────────────────────────────────────────────

def test_admin_drive_sync_requires_admin(client, auth_header) -> None:
    resp = client.post("/api/admin/drive-sync/run", headers=auth_header)
    assert resp.status_code == 403


def test_admin_drive_sync_503_when_unconfigured(
    client, admin_header, monkeypatch
) -> None:
    for v in ("MSGRAPH_TENANT_ID", "MSGRAPH_CLIENT_ID", "MSGRAPH_CLIENT_SECRET", "MSGRAPH_DRIVE_ID"):
        monkeypatch.delenv(v, raising=False)
    resp = client.post("/api/admin/drive-sync/run", headers=admin_header)
    assert resp.status_code == 503
