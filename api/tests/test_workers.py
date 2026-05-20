"""Tests for worker polling reentrancy and MCP response parsing.

Both `api.workers.campaign_worker.run_worker` and `fp_worker.run_worker`
guard their poll cycle with a module-level `_in_flight` flag. CLAUDE.md
explicitly calls out this pattern: an `in_flight` flag must be set on
entry, cleared in `finally`, so a slow DB call cannot make the next
poll fire before the previous one returns.

These tests run the poll loop in a controlled way and assert the guard
holds under concurrent triggers.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from api.workers import campaign_worker, fp_worker

# ── Reentrancy guard (campaign_worker) ───────────────────────────────────────


@pytest.mark.asyncio
async def test_campaign_worker_in_flight_prevents_overlap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If `_in_flight` is True when the poll fires, the cycle must be
    skipped — even if both `process_retry_steps` and
    `process_running_campaigns` would otherwise be called."""
    call_count = {"retry": 0, "running": 0}

    async def _fake_retry(db: Any) -> int:
        call_count["retry"] += 1
        return 0

    async def _fake_running(db: Any, factory: Any) -> int:
        call_count["running"] += 1
        return 0

    # Shortcut session_factory: we never actually open one, but the worker
    # signature requires it. Use a no-op async context manager.
    class _NoopSession:
        async def __aenter__(self) -> Any:
            return self
        async def __aexit__(self, *a: Any) -> None:
            return None

    def _factory() -> _NoopSession:
        return _NoopSession()

    monkeypatch.setattr(campaign_worker, "process_retry_steps", _fake_retry)
    monkeypatch.setattr(campaign_worker, "process_running_campaigns", _fake_running)
    monkeypatch.setattr(campaign_worker, "POLL_INTERVAL_SECONDS", 0.01)

    # Pre-set the in-flight flag, then run a few cycles. None should hit
    # the inner work functions.
    campaign_worker._in_flight = True
    task = asyncio.create_task(campaign_worker.run_worker(_factory))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    finally:
        campaign_worker._in_flight = False

    assert call_count["retry"] == 0
    assert call_count["running"] == 0


@pytest.mark.asyncio
async def test_campaign_worker_in_flight_cleared_in_finally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Even when the inner work raises, the in-flight flag must be cleared
    so the next cycle can run. This is the CLAUDE.md rule: clear in finally."""
    async def _boom_retry(db: Any) -> int:
        raise RuntimeError("simulated retry failure")

    async def _ok_running(db: Any, factory: Any) -> int:
        return 0

    class _NoopSession:
        async def __aenter__(self) -> Any:
            return self
        async def __aexit__(self, *a: Any) -> None:
            return None

    def _factory() -> _NoopSession:
        return _NoopSession()

    monkeypatch.setattr(campaign_worker, "process_retry_steps", _boom_retry)
    monkeypatch.setattr(campaign_worker, "process_running_campaigns", _ok_running)
    monkeypatch.setattr(campaign_worker, "POLL_INTERVAL_SECONDS", 0.01)

    campaign_worker._in_flight = False
    task = asyncio.create_task(campaign_worker.run_worker(_factory))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    # The flag must be clear at the end despite the inner raise.
    assert campaign_worker._in_flight is False


# ── Reentrancy guard (fp_worker) ─────────────────────────────────────────────


@pytest.mark.asyncio
async def test_fp_worker_in_flight_cleared_in_finally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _boom(db: Any) -> int:
        raise RuntimeError("simulated compute failure")

    class _NoopSession:
        async def __aenter__(self) -> Any:
            return self
        async def __aexit__(self, *a: Any) -> None:
            return None

    def _factory() -> _NoopSession:
        return _NoopSession()

    monkeypatch.setattr(fp_worker, "compute_compound_fingerprints", _boom)
    monkeypatch.setattr(fp_worker, "compute_reaction_fingerprints", _boom)
    monkeypatch.setattr(fp_worker, "POLL_INTERVAL_SECONDS", 0.01)

    fp_worker._in_flight = False
    task = asyncio.create_task(fp_worker.run_worker(_factory))
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert fp_worker._in_flight is False


# ── fp_worker._stable_lock_key ───────────────────────────────────────────────


def test_stable_lock_key_is_deterministic_and_fits_bigint() -> None:
    """The advisory-lock key must be stable across processes (no PYTHONHASHSEED)
    and fit in a positive BIGINT (i.e. fit in 63 bits)."""
    uid = "12345678-1234-5678-1234-567812345678"
    a = fp_worker._stable_lock_key(uid)
    b = fp_worker._stable_lock_key(uid)
    assert a == b
    assert 0 <= a < 2**63


def test_stable_lock_key_rejects_non_uuid() -> None:
    with pytest.raises(ValueError):
        fp_worker._stable_lock_key("not-a-uuid")


# ── fp_worker._call_mcp_tool (MCP response parsing) ──────────────────────────


class _StubProc:
    """Stand-in for asyncio.subprocess.Process used by _call_mcp_tool."""

    def __init__(self, stdout: bytes, stderr: bytes = b"") -> None:
        self._stdout = stdout
        self._stderr = stderr

    async def communicate(self, input: bytes | None = None) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass

    async def wait(self) -> int:
        return 0


def _patch_subprocess(monkeypatch: pytest.MonkeyPatch, proc: _StubProc) -> None:
    async def _fake_exec(*a: Any, **kw: Any) -> _StubProc:
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_exec)


@pytest.mark.asyncio
async def test_call_mcp_tool_parses_valid_response(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {"fingerprint_bits": "0" * 2048}
    response = {
        "jsonrpc": "2.0", "id": 1,
        "result": {"content": [{"type": "text", "text": json.dumps(payload)}]},
    }
    _patch_subprocess(monkeypatch, _StubProc(json.dumps(response).encode() + b"\n"))
    out = await fp_worker._call_mcp_tool("mcp_molfp.server", "compute_morgan_fp", {"smiles": "CCO"})
    assert out == payload


@pytest.mark.asyncio
async def test_call_mcp_tool_malformed_outer_json_is_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A line that starts with '{' but isn't valid JSON must not crash the
    worker — it should be skipped and the function should fall through to
    the unparseable RuntimeError."""
    _patch_subprocess(monkeypatch, _StubProc(b"{not valid json\n"))
    with pytest.raises(RuntimeError, match="Could not parse"):
        await fp_worker._call_mcp_tool("mcp_molfp.server", "compute_morgan_fp", {"smiles": "CCO"})


@pytest.mark.asyncio
async def test_call_mcp_tool_invalid_inner_text_block_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If the outer JSON is valid but the inner text block doesn't parse
    as JSON, raise a RuntimeError with the server/tool tag — don't
    surface the unhandled JSONDecodeError up the worker loop."""
    response = {
        "jsonrpc": "2.0", "id": 1,
        "result": {"content": [{"type": "text", "text": "not-json-at-all"}]},
    }
    _patch_subprocess(monkeypatch, _StubProc(json.dumps(response).encode() + b"\n"))
    with pytest.raises(RuntimeError, match="invalid JSON"):
        await fp_worker._call_mcp_tool("mcp_molfp.server", "compute_morgan_fp", {"smiles": "CCO"})


@pytest.mark.asyncio
async def test_call_mcp_tool_missing_text_block_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = {"jsonrpc": "2.0", "id": 1, "result": {"content": []}}
    _patch_subprocess(monkeypatch, _StubProc(json.dumps(response).encode() + b"\n"))
    with pytest.raises(RuntimeError, match="No text block"):
        await fp_worker._call_mcp_tool("mcp_molfp.server", "compute_morgan_fp", {"smiles": "CCO"})


# ── _create_campaign_wiki retry behaviour ────────────────────────────────────


def _noop_factory() -> Any:
    """Stand-in session_factory: tests never reach the DB because
    upsert_wiki_page is mocked."""
    class _NoopSession:
        async def __aenter__(self) -> Any:
            return self
        async def __aexit__(self, *a: Any) -> None:
            return None
    return _NoopSession()


@pytest.mark.asyncio
async def test_create_campaign_wiki_succeeds_first_try(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    async def _fake_upsert(*a: Any, **kw: Any) -> str:
        calls["n"] += 1
        return "page-id"

    monkeypatch.setattr("api.db.queries.wiki_write.upsert_wiki_page", _fake_upsert)

    out = await campaign_worker._create_campaign_wiki(
        {"id": "c-1", "target_smiles": "CCO", "plan": {"steps": []}, "created_by": "alice"},
        _noop_factory,
    )
    assert out == {"ok": True, "error": None}
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_create_campaign_wiki_retries_then_succeeds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Two transient failures + one success → ok=True after 3 attempts."""
    calls = {"n": 0}

    async def _flaky_upsert(*a: Any, **kw: Any) -> str:
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("transient embed failure")
        return "page-id"

    # Skip the real sleeps so the test stays fast.
    async def _no_sleep(*a: Any, **kw: Any) -> None:
        return None

    monkeypatch.setattr("api.db.queries.wiki_write.upsert_wiki_page", _flaky_upsert)
    monkeypatch.setattr(campaign_worker.asyncio, "sleep", _no_sleep)

    out = await campaign_worker._create_campaign_wiki(
        {"id": "c-2", "target_smiles": "CCO", "plan": {}, "created_by": "alice"},
        _noop_factory,
    )
    assert out == {"ok": True, "error": None}
    assert calls["n"] == 3


@pytest.mark.asyncio
async def test_create_campaign_wiki_exhausts_retries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """All attempts fail → ok=False with the last error string. The first
    attempt has no delay; the remaining attempts are 3 retries → 4 tries total."""
    calls = {"n": 0}

    async def _always_fail(*a: Any, **kw: Any) -> str:
        calls["n"] += 1
        raise RuntimeError("permanent failure")

    async def _no_sleep(*a: Any, **kw: Any) -> None:
        return None

    monkeypatch.setattr("api.db.queries.wiki_write.upsert_wiki_page", _always_fail)
    monkeypatch.setattr(campaign_worker.asyncio, "sleep", _no_sleep)

    out = await campaign_worker._create_campaign_wiki(
        {"id": "c-3", "target_smiles": "CCO", "plan": {}, "created_by": "alice"},
        _noop_factory,
    )
    assert out["ok"] is False
    assert "permanent failure" in (out["error"] or "")
    # 1 initial attempt + 3 retries = 4 tries (matches _WIKI_RETRY_DELAYS_SEC).
    assert calls["n"] == 4
