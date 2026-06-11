"""Prometheus metrics — counters/histograms/gauges + `/metrics` endpoint.

Postgres-first per CLAUDE.md operating principles: prometheus-client is
a 13 KB pure-Python wheel with zero transitive deps, no agent process,
no external service. The scrape endpoint exposes the default registry;
operators point their existing Prometheus / Grafana Cloud / Datadog
agent at it.

Counters live as module-level singletons so the metric definitions
survive `configure_logging` reloads and worker subprocess boundaries.
The names follow the Prometheus naming convention (snake_case +
`_total`/`_seconds`/`_size` suffixes).
"""
from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram

# ── HTTP request counters / histograms ───────────────────────────────────────

http_requests_total = Counter(
    "http_requests_total",
    "HTTP requests handled, by route + method + status",
    labelnames=("route", "method", "status"),
)

http_request_duration_seconds = Histogram(
    "http_request_duration_seconds",
    "HTTP request latency, by route + method",
    labelnames=("route", "method"),
    # Sub-second granularity for the chat/wiki path, coarser at the tail
    # for the long agent runs.
    buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0, 30.0),
)

# ── Security / rate-limit signals ────────────────────────────────────────────

rate_limit_blocked_total = Counter(
    "rate_limit_blocked_total",
    "Rate-limit denials (429s), by bucket",
    labelnames=("bucket",),
)

substance_gate_blocked_total = Counter(
    "substance_gate_blocked_total",
    "Scheduled-substance gate denials. Always desirable when nonzero — "
    "indicates the gate is working.",
)

# ── Worker queue depth (gauges, polled) ──────────────────────────────────────

fp_worker_backlog = Gauge(
    "fp_worker_backlog",
    "Pending fingerprint compute rows. >5000 triggers readiness=degraded.",
    labelnames=("kind",),  # compound | reaction
)
