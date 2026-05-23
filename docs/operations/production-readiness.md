# Production-readiness checklist

The exit gate for the May-2026 hardening initiative (waves 0–8). Each
item links to the wave that landed it. Recheck before any major
push (new deployment region, traffic order-of-magnitude jump).

## Static analysis

- [x] `ruff check` gates CI with rules `E, F, W, I, UP, B` (wave 3)
- [x] `mypy api/ tools/` gates CI, no per-module `ignore_errors`
      except the upstream `claude_agent_sdk.*` (wave 3)
- [x] `pip-audit` runs on every PR (wave 3 — informational; flip to
      gating after 5 clean runs on main)

## Tests

- [x] Coverage tooling wired; coverage.xml produced as a build
      artifact on the cheap lane (wave 2)
- [x] Route-layer integration tests for every router that mutates
      state (wave 2 + pre-existing)
- [x] Observability + readiness + request-id middleware tests (wave 5)
- [x] Migration linter has its own tests (wave 6)
- [ ] Coverage threshold `--cov-fail-under=75` (next wave)

## Build & packaging

- [x] MCP wheel layouts fixed (mcp_molfp / mcp_rxnfp / mcp_retrosynth
      now produce code-bearing wheels) (wave 4)
- [x] `Dockerfile` HEALTHCHECK present (wave 4)
- [x] `docker-compose.yml` for local dev (wave 7)
- [ ] Lockfile committed (next wave: `uv.lock`)

## Deployment

- [x] Uvicorn production tuning: --workers 2, --timeout-keep-alive,
      --timeout-graceful-shutdown, --proxy-headers (wave 4)
- [x] DB pool tunable via env (`DB_POOL_SIZE`, `DB_POOL_MAX_OVERFLOW`) (wave 4)
- [x] CORS fails closed in prod (wave 1)
- [x] Liveness vs readiness probe split (wave 5)
- [x] Deployment runbook (`docs/operations/deployment.md`) (wave 7)
- [x] Incident runbook (`docs/operations/runbook.md`) (wave 7)

## Observability

- [x] Structured logging (`LOG_FORMAT=json`) (wave 5)
- [x] Request-id middleware (`X-Request-ID` round-trip) (wave 5)
- [x] /api/readiness reflects DB + worker backlog (wave 5)
- [ ] Prometheus `/metrics` endpoint (next wave: prometheus-client)
- [ ] Worker queue-depth gauge (next wave)

## Security

- [x] Generic error messages from agent tools (wave 1)
- [x] CORS prod-gate (wave 1)
- [x] B904 `raise … from None` in 401/403/422 paths (wave 3)
- [x] Wiki ownership-helper extracted (wave 1)
- [x] SSRF allowlist audit script (`tools/audit_allowlist.py`) (wave 6)
- [x] Migration linter (`tools/check_migrations.py`) (wave 6)
- [x] `_SECRET_PATTERNS` covers Stripe, JWT, AWS, GitHub PAT, Slack,
      Google, GitLab, SendGrid, Twilio, npm, Anthropic (pre-existing,
      audited wave 1)

## Documentation

- [x] README.md with quick start, architecture, deploy summary (wave 7)
- [x] `.env.example` covers every read of `os.environ` (wave 7)
- [x] `docs/architecture/overview.md` (wave 7)
- [x] `docs/adr/` records for the two architecture choices (wave 7)
- [x] `docs/deferred/` for every Tier F item with trigger + checklist (wave 8)
- [x] `MIGRATIONS.md` policy documented + enforced by linter (pre-existing + wave 6)

## Deferred (explicit, with re-enable checklist)

- [x] Multi-tenant RLS — see `docs/deferred/multi-tenant-rls.md`
- [x] ORD export — see `docs/deferred/ord-export.md`
- [x] ML property predictions — see `docs/deferred/ml-property-predictions.md`
- [x] Tool forging — see `docs/deferred/tool-forging.md`
- [x] ELN fetch-path verification — see `docs/deferred/eln-fetch-verification.md`

## Open follow-ups for next initiative

- Lockfile (uv) for reproducible installs.
- Prometheus `/metrics` endpoint + worker queue-depth gauge.
- X-RateLimit-{Limit,Remaining,Reset} headers on every rate-limited route.
- Coverage threshold gate in CI.
- mypy strict mode (currently non-strict but `check_untyped_defs=True`).
- Flip the heavy CI lane + pip-audit from `continue-on-error: true`
  to gating once each has 5 consecutive clean runs on main.
