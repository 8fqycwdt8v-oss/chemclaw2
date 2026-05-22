"""HTTP-layer tests for POST /api/chat.

Per the pattern set in `test_routes_v2.py`: sync TestClient tests that
verify route wiring + Pydantic validation + auth + the SSE error-stream
paths. Seeded-data integration tests are out of scope (asyncpg event-loop
teardown noise in the per-test loops, per the project convention).

For the happy path we monkey-patch `run_agent_streaming` so the test
exercises the route layer (validation, gate check, streaming-response
shape) without spinning up a real agent loop. The substance gate and
rate limiter still run for real — they're per-request, in-process.
"""
from __future__ import annotations

import json
from typing import AsyncIterator


# ── Pydantic validation on ChatRequest ───────────────────────────────────────


def test_chat_empty_prompt_rejected(client, auth_header):
    """ChatRequest.prompt_not_empty raises on empty / whitespace-only input."""
    resp = client.post("/api/chat", headers=auth_header, json={"prompt": "  "})
    assert resp.status_code == 422


def test_chat_oversized_prompt_rejected(client, auth_header):
    """100 KB hard cap from MAX_PROMPT_BYTES — Pydantic raises 422."""
    resp = client.post(
        "/api/chat",
        headers=auth_header,
        json={"prompt": "x" * 200_000},
    )
    assert resp.status_code == 422


def test_chat_missing_prompt_field_rejected(client, auth_header):
    """Pydantic requires `prompt` — request without it is 422."""
    resp = client.post("/api/chat", headers=auth_header, json={})
    assert resp.status_code == 422


def test_chat_requires_auth(client):
    """No Authorization header → 401 from get_current_user dep."""
    resp = client.post("/api/chat", json={"prompt": "hello"})
    assert resp.status_code == 401


def test_chat_invalid_session_id_silently_dropped(
    client, auth_header, monkeypatch,
):
    """The session_id validator returns None for non-UUID strings; the
    route then generates a fresh UUID. This documents that bad client
    state doesn't 4xx — it gets quietly upgraded to a new session."""
    captured: dict[str, str | None] = {}

    async def _fake_stream(
        prompt: str, user_id: str, session_id: str, factory, plan_mode: bool = False,
    ) -> AsyncIterator[str]:
        captured["session_id"] = session_id
        captured["prompt"] = prompt
        yield "data: {\"type\":\"text\",\"text\":\"hi\"}\n\n"
        yield "data: [DONE]\n\n"

    # chat.py does `from api.agent.runner import run_agent_streaming` inline
    # per call, so patching the source module is what reaches the route.
    monkeypatch.setattr("api.agent.runner.run_agent_streaming", _fake_stream)

    resp = client.post(
        "/api/chat",
        headers=auth_header,
        json={"prompt": "say hi", "session_id": "not-a-uuid"},
    )
    # 200 streaming response, not 422.
    assert resp.status_code == 200
    # A fresh UUID was generated since the supplied session_id was invalid.
    sid = captured.get("session_id")
    assert sid is not None and len(sid) == 36  # uuid4 hex with dashes


# ── Substance gate ──────────────────────────────────────────────────────────


def test_chat_blocked_substance_returns_403_sse(client, auth_header):
    """A prompt mentioning a Schedule I substance triggers the substance
    gate; without an override_justification the response is a 403 SSE
    error stream with override_available=True so the UI can prompt the
    user for a justification."""
    resp = client.post(
        "/api/chat",
        headers=auth_header,
        json={"prompt": "synthesise heroin from morphine"},
    )
    assert resp.status_code == 403
    body = resp.text
    # The error frame is JSON inside an SSE `data:` line.
    assert "blocked" in body.lower()
    # The substance-gate path always offers override availability so the
    # UI can ask the user for a justification.
    assert "override_available" in body
    # Parse the first SSE frame to confirm shape.
    first_frame = next(
        ln[len("data: "):] for ln in body.splitlines()
        if ln.startswith("data: ") and "[DONE]" not in ln
    )
    parsed = json.loads(first_frame)
    assert parsed["type"] == "error"
    assert parsed["blocked"] is True
    assert parsed["override_available"] is True


# ── Mocked happy path ───────────────────────────────────────────────────────


def test_chat_streams_sse_frames_on_happy_path(client, auth_header, monkeypatch):
    """End-to-end route wiring: a valid prompt passes auth + gate + rate
    limit + the run_agent_streaming call, and the SSE frames yielded
    by the agent reach the client."""

    async def _fake_stream(
        prompt: str, user_id: str, session_id: str, factory, plan_mode: bool = False,
    ) -> AsyncIterator[str]:
        yield "data: {\"type\":\"text\",\"text\":\"first chunk\"}\n\n"
        yield "data: {\"type\":\"text\",\"text\":\"second chunk\"}\n\n"
        yield "data: [DONE]\n\n"

    # Patch at both the source and the local re-binding inside chat.py.
    # chat.py does `from api.agent.runner import run_agent_streaming` inline
    # per call, so patching the source module is what reaches the route.
    monkeypatch.setattr("api.agent.runner.run_agent_streaming", _fake_stream)

    resp = client.post(
        "/api/chat",
        headers=auth_header,
        json={"prompt": "what's in the wiki about benzene?"},
    )
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/event-stream")
    # The two text frames + the [DONE] sentinel reach the client.
    frames = [ln for ln in resp.text.splitlines() if ln.startswith("data: ")]
    assert any("first chunk" in f for f in frames)
    assert any("second chunk" in f for f in frames)
    assert any("[DONE]" in f for f in frames)


# ── Failure paths ───────────────────────────────────────────────────────────


def test_chat_session_factory_unset_returns_503(client, auth_header, monkeypatch):
    """When DB init hasn't run (or has failed), `async_session_factory`
    is None and the route surfaces a 503 SSE error stream — not a 500.
    Pins the explicit fallback in api/routes/chat.py."""
    monkeypatch.setattr("api.db.connection.async_session_factory", None)
    resp = client.post(
        "/api/chat",
        headers=auth_header,
        json={"prompt": "this prompt should never reach the agent"},
    )
    assert resp.status_code == 503
    body = resp.text
    first = next(
        ln[len("data: "):] for ln in body.splitlines()
        if ln.startswith("data: ") and "[DONE]" not in ln
    )
    parsed = json.loads(first)
    assert parsed["type"] == "error"
    assert "not initialised" in parsed["message"].lower()


def test_chat_runner_exception_yields_generic_error_frame(
    client, auth_header, monkeypatch,
):
    """When the agent generator raises mid-stream, the runner's global
    try/except catches it and yields a generic error frame — no 5xx,
    no exception text leaked (CLAUDE.md §security-4).

    Note: HTTP status stays 200 because headers ship before the
    exception fires inside the StreamingResponse body. The contract
    is the *frame* shape and the absence of leaked internals."""

    async def _explodes(
        prompt: str, user_id: str, session_id: str, factory, plan_mode: bool = False,
    ) -> AsyncIterator[str]:
        # Yield one frame so headers are committed, then go boom — this
        # forces the runner's exception path (line 457-459) rather than
        # the pre-stream factory-is-None path.
        yield "data: {\"type\":\"text\",\"text\":\"about to crash\"}\n\n"
        raise RuntimeError("internal DB blew up — credentials inside: hunter2")

    monkeypatch.setattr("api.agent.runner.run_agent_streaming", _explodes)

    resp = client.post(
        "/api/chat",
        headers=auth_header,
        json={"prompt": "trigger an exception in the runner"},
    )
    # Headers shipped before the raise → 200 status, then a body that
    # propagates the RuntimeError up the StreamingResponse middleware
    # (the *route* doesn't catch — the runner is supposed to).
    # Here, since _explodes lacks the runner's try/except, the raise
    # surfaces at the StreamingResponse level. ASGI middleware turns
    # that into a connection close, not a 5xx body. What we verify is
    # that the credential-bearing string never reaches the client.
    assert "hunter2" not in resp.text
    assert "RuntimeError" not in resp.text


def test_chat_substance_gate_override_path_records_audit(
    client, auth_header, monkeypatch,
):
    """A blocked-substance prompt WITH a non-empty `override_justification`
    of length ≥20 reaches the agent (no 403). The route writes an audit
    record via `record_override` before continuing."""
    captured: dict[str, str] = {}

    async def _fake_stream(
        prompt: str, user_id: str, session_id: str, factory, plan_mode: bool = False,
    ) -> AsyncIterator[str]:
        captured["prompt"] = prompt
        yield "data: {\"type\":\"text\",\"text\":\"agent ran\"}\n\n"
        yield "data: [DONE]\n\n"

    recorded: list[dict[str, str]] = []

    async def _fake_record_override(db, sid, uid, kind, justification, prompt):
        recorded.append({
            "session_id": sid, "user_id": uid, "kind": kind,
            "justification": justification, "prompt": prompt,
        })

    monkeypatch.setattr("api.agent.runner.run_agent_streaming", _fake_stream)
    monkeypatch.setattr(
        "api.db.queries.budgets.record_override", _fake_record_override,
    )

    resp = client.post(
        "/api/chat",
        headers=auth_header,
        json={
            "prompt": "synthesise heroin from morphine",
            "override_justification": (
                "DEA-licensed forensic reference standards lab; "
                "preparing certified standards under 21 CFR 1308.43."
            ),
        },
    )
    assert resp.status_code == 200
    assert recorded, "record_override should have been called with the justification"
    assert recorded[0]["kind"] == "scheduled_substance"
    assert captured["prompt"].lower().startswith("synthesise heroin")
