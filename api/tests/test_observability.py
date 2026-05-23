"""Tests for the observability primitives: request-id propagation,
readiness vs liveness split, structured-log JSON emission.
"""
from __future__ import annotations

import json
import logging

from api.observability.logging import (
    bind_request_id,
    configure_logging,
    get_request_id,
    reset_request_id,
)

# ── Request-id header round-trip ─────────────────────────────────────────────

def test_health_returns_request_id_header(client, auth_header):
    """The middleware mints an id when the client didn't supply one and
    echoes it back so the client can correlate."""
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.headers.get("X-Request-ID")
    assert len(r.headers["X-Request-ID"]) <= 64


def test_inbound_request_id_preserved(client):
    r = client.get(
        "/api/health",
        headers={"X-Request-ID": "test-trace-123"},
    )
    assert r.status_code == 200
    assert r.headers["X-Request-ID"] == "test-trace-123"


def test_inbound_request_id_overlong_dropped(client):
    """An oversized inbound id is replaced with a fresh uuid4 so log
    columns aren't blown out by hostile callers."""
    r = client.get(
        "/api/health",
        headers={"X-Request-ID": "x" * 200},
    )
    assert r.status_code == 200
    assert r.headers["X-Request-ID"] != "x" * 200
    assert len(r.headers["X-Request-ID"]) <= 64


def test_inbound_request_id_unsafe_chars_dropped(client):
    r = client.get(
        "/api/health",
        headers={"X-Request-ID": "trace with spaces"},
    )
    assert r.status_code == 200
    assert " " not in r.headers["X-Request-ID"]


# ── Liveness vs readiness split ──────────────────────────────────────────────

def test_health_is_pure_liveness(client):
    """Liveness must not depend on the DB — a brief DB blip must not
    cause the orchestrator to restart the process. Probe returns 200
    even when no DB is wired."""
    r = client.get("/api/health")
    assert r.status_code == 200
    body = r.json()
    assert body == {"ok": True}


def test_readiness_returns_diagnostic_payload(client):
    """Readiness exposes DB + backlog so dashboards can chart it.
    Status code 200 means routable; 503 means drain. The body always
    carries the keys regardless of status."""
    r = client.get("/api/readiness")
    assert r.status_code in (200, 503)
    body = r.json()
    assert "ready" in body
    assert "db" in body
    assert "fingerprint_backlog" in body
    assert "compounds" in body["fingerprint_backlog"]
    assert "reactions" in body["fingerprint_backlog"]


# ── Contextvars binding ──────────────────────────────────────────────────────

def test_request_id_contextvar_isolation():
    """`bind_request_id` returns a token that resets cleanly so the
    binding is request-scoped, not process-scoped."""
    assert get_request_id() is None
    token = bind_request_id("rid-1")
    try:
        assert get_request_id() == "rid-1"
    finally:
        reset_request_id(token)
    assert get_request_id() is None


# ── JSON formatter ───────────────────────────────────────────────────────────

def test_json_formatter_emits_single_line(capsys):
    """A JSON log line must be one self-contained JSON object so log
    aggregators can split on newlines."""
    configure_logging(level="INFO", fmt="json")
    token = bind_request_id("rid-json-test")
    try:
        logger = logging.getLogger("api.observability.test")
        logger.info("hello", extra={"user_id": "u-x", "route": "/x"})
    finally:
        reset_request_id(token)
        # Restore the test-default formatter so other tests aren't
        # surprised by JSON-formatted output landing in their assertions.
        configure_logging(level="INFO", fmt="plain")
    captured = capsys.readouterr()
    lines = [ln for ln in captured.out.splitlines() if ln.startswith("{")]
    assert lines, "expected at least one JSON-formatted log line"
    payload = json.loads(lines[-1])
    assert payload["message"] == "hello"
    assert payload["request_id"] == "rid-json-test"
    assert payload["user_id"] == "u-x"
    assert payload["route"] == "/x"
    assert payload["level"] == "INFO"


def test_json_formatter_handles_unserialisable_extras(capsys):
    """An object that can't be JSON-serialised falls back to str() rather
    than dropping the whole log line."""
    configure_logging(level="INFO", fmt="json")

    class _Weird:
        def __str__(self) -> str:
            return "weird-instance"

    try:
        logger = logging.getLogger("api.observability.test")
        logger.info("weird", extra={"obj": _Weird()})
    finally:
        configure_logging(level="INFO", fmt="plain")
    captured = capsys.readouterr()
    lines = [ln for ln in captured.out.splitlines() if ln.startswith("{")]
    assert lines
    payload = json.loads(lines[-1])
    assert payload["obj"] == "weird-instance"
