"""Observability primitives: structured logging, request IDs, metrics.

Library-driven per CLAUDE.md operating principles — no new services
(Datadog/Sentry/etc.). Postgres-first; logs to stdout, metrics scraped
from `/metrics`, request-id correlation via contextvars.
"""
