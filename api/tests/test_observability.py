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


def test_access_log_carries_request_id(client, capsys):
    """Regression guard — the access log line MUST land while the request-id
    contextvar is still bound. A previous version cleared the contextvar in
    `finally` before the log emit, leaking `-` instead of the real id."""
    configure_logging(level="INFO", fmt="json")
    try:
        r = client.get(
            "/api/wiki",  # any non-probe route so the access log fires
            headers={"X-Request-ID": "rid-access-log-test"},
        )
    finally:
        configure_logging(level="INFO", fmt="plain")
    # Response header confirms the middleware bound the id correctly.
    assert r.headers["X-Request-ID"] == "rid-access-log-test"
    captured = capsys.readouterr()
    access_lines = [
        ln for ln in captured.out.splitlines()
        if ln.startswith("{") and '"request_complete"' in ln
    ]
    assert access_lines, "expected a request_complete access log line"
    payload = json.loads(access_lines[-1])
    assert payload["request_id"] == "rid-access-log-test"
    assert payload["route"] == "/api/wiki"


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
    # `ts` must be a real ISO-8601 timestamp, not the literal `%f` —
    # `logging.Formatter.formatTime` delegates to `strftime` which doesn't
    # understand `%f`, so the previous implementation embedded the literal
    # directive in the payload.
    assert "%f" not in payload["ts"]
    assert payload["ts"].endswith("+00:00") or payload["ts"].endswith("Z")


# ── X-RateLimit-* headers ────────────────────────────────────────────────────

def test_rate_limit_headers_on_happy_path(client, auth_header):
    """Successful requests through a rate-limited route carry the
    X-RateLimit-{Limit,Remaining,Reset} + Retry-After triplet so the
    client can pre-empt 429s."""
    r = client.get("/api/wiki", headers=auth_header)
    # 200 or 503 (DB blip); both are happy-path responses for the dep.
    if r.status_code in (200, 503):
        assert "X-RateLimit-Limit" in r.headers
        assert "X-RateLimit-Remaining" in r.headers
        assert "X-RateLimit-Reset" in r.headers
        # Limit is a positive int.
        assert int(r.headers["X-RateLimit-Limit"]) > 0
        # Remaining is non-negative and ≤ Limit.
        assert 0 <= int(r.headers["X-RateLimit-Remaining"]) <= int(r.headers["X-RateLimit-Limit"])


# ── Prometheus metrics ───────────────────────────────────────────────────────

def test_metrics_endpoint_returns_prometheus_exposition(client):
    """The `/metrics` endpoint returns the standard Prometheus text format
    so a scrape agent (Grafana Agent, vmagent, prometheus itself) can
    consume it without any adapter."""
    r = client.get("/metrics")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/plain")
    body = r.text
    # Counters appear as `# HELP` + `# TYPE` blocks. The wiring is correct
    # if our metric names show up in the exposition.
    assert "http_requests_total" in body
    assert "rate_limit_blocked_total" in body
    assert "substance_gate_blocked_total" in body


def test_http_request_metric_increments(client):
    """A real request through the stack increments the http_requests_total
    counter for that (route, method, status) label triple."""
    from api.observability.metrics import http_requests_total
    # Fire a known-to-succeed probe.
    r = client.get("/api/health")
    assert r.status_code == 200
    # The label-set has to match the same labelnames the middleware uses;
    # status comes back as int in our middleware, so it's stringified by
    # prometheus_client in the exposition. Read the metric back via the
    # private `_value` accessor — stable across prometheus_client versions
    # for Counter children.
    child = http_requests_total.labels(route="/api/health", method="GET", status=200)
    assert child._value.get() >= 1


def test_http_request_metric_unmatched_routes_collapse(client):
    """Requests that match no route (404 probes) collapse into a single
    "unmatched" label instead of minting one label per raw path — raw paths
    embed UUIDs/scanner noise and would grow metric cardinality unbounded."""
    from api.observability.metrics import http_requests_total
    r1 = client.get("/no-such-route-abc123")
    r2 = client.get("/another/bogus/eb8d11d2-1111-2222-3333-444455556666")
    assert r1.status_code == 404
    assert r2.status_code == 404
    child = http_requests_total.labels(route="unmatched", method="GET", status=404)
    assert child._value.get() >= 2


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
