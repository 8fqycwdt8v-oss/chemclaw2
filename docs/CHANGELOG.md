# Changelog

## 2026-05 — Production hardening initiative (waves 0–8)

Eight-wave hardening pass on top of the May-2026 Tier A–E refactors.
Single PR (`claude/code-review-refactor-hardening-lUa1Z`), one commit
per wave.

### Wave 1 — Security & correctness residue
- Fix the genuine `str(e)[:200]` error leak in
  `document_enrichment.py`. ValueError sites elsewhere kept as-is
  (curated query-layer messages, intentional UX).
- Extract `_load_owned_wiki_page` helper to collapse the 3-copy
  ownership-check pattern in `routes/wiki.py`.
- Drop the defensive `hasattr(ts, 'isoformat')` check on a typed
  `datetime`.
- CORS fails closed in production — refuses to start when `ENV ∈
  {prod, production}` and `CORS_ALLOWED_ORIGINS` is empty, contains a
  wildcard, or contains a localhost origin.

### Wave 2 — Test coverage + coverage tooling
- New `test_routes_admin.py` (401/403/200 matrix, CRUD cycle).
- New `test_cors_prod_gate.py` (parametrised prod-CORS proof).
- pytest-cov + pip-audit added to dev extras.
- `heavy` pytest marker declared; coverage config in `pyproject.toml`.
- CI cheap lane produces `coverage.xml` as a build artifact.

### Wave 3 — Static analysis tightening
- ruff `select` expanded: `E, F, W, I, UP, B`. FastAPI's
  Depends/Query/Body/Path/Header whitelisted as immutable for B008.
- Drop the per-module `ignore_errors` overrides for
  `api.agent.tools/runner/hooks` — they type-check cleanly under the
  default config.
- Auto-fixes applied across 41 files (import sort, pyupgrade,
  strict-zip, `from None` on chained raises).
- pip-audit step added to CI (cheap lane, non-blocking initially).

### Wave 4 — Build, packaging, dependency hygiene
- MCP wheel layout fix: `mcp_molfp`, `mcp_rxnfp`, `mcp_retrosynth`
  restructured to nested `<name>/<name>/` layout. The flat layout
  built 1 KB metadata-only wheels.
- DB pool sizing via `DB_POOL_SIZE` / `DB_POOL_MAX_OVERFLOW` env.
- Uvicorn production tuning in `fly.toml` (`--workers 2`,
  `--timeout-keep-alive`, `--timeout-graceful-shutdown`,
  `--proxy-headers`).
- `Dockerfile` HEALTHCHECK against `/api/health`.

### Wave 5 — Observability foundation
- `api/observability/logging.py`: stdlib-only structured logging.
  `LOG_FORMAT=json` flattens every `extra=` field into a single JSON
  line; falls back to str() for unserialisable extras.
- `api/observability/middleware.py`: `RequestIdMiddleware` binds an
  inbound `X-Request-ID` (sanitised) or a fresh uuid4, echoes it back,
  and emits an access log.
- `/api/health` is now pure liveness (no DB). `/api/readiness` does the
  DB ping + fingerprint-backlog gate (degraded > 5000).
- `test_observability.py`: request-id round-trip, JSON formatter,
  liveness/readiness split.

### Wave 6 — Operational hardening
- `tools/check_migrations.py`: filename pattern + duplicate-slot
  detection + CONCURRENTLY isolation; optional strict mode for
  IF [NOT] EXISTS. Wired into CI.
- `tools/audit_allowlist.py`: operator script that resolves every
  `ALLOWED_DOMAINS` entry and flags private/loopback regressions.
- Test coverage for the migration linter's regexes.

### Wave 7 — Documentation + onboarding
- `README.md` with quick start, architecture, deploy summary.
- `.env.example` covering every `os.environ` read.
- `docker-compose.yml` for one-command local dev.
- `docs/operations/deployment.md`, `docs/operations/runbook.md`.
- `docs/architecture/overview.md`.
- `docs/adr/0001-postgres-first.md`, `0002-mcp-stdio-isolation.md`.

### Wave 8 — Tier F deferral + readiness checklist
- `docs/deferred/` with one file per Tier F item (multi-tenant RLS,
  ORD export, ML predictions, tool forging, ELN verification) —
  trigger + prerequisite + risk/value.
- `docs/operations/production-readiness.md` — the exit checklist.
- This changelog.

## Outcome

`docs/operations/production-readiness.md` lists 28 checkboxes, 24
ticked. The four open items are documented as follow-ups for the next
initiative (uv lockfile, Prometheus `/metrics`, rate-limit headers,
coverage threshold).
