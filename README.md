# chemclaw2

Knowledge-intelligence agent for pharma R&D. Three surfaces: conversational
agent, living wiki, chemistry-native search. FastAPI + SQLAlchemy 2.0 async +
Postgres/pgvector + Claude Agent SDK + six MCP servers.

`CLAUDE.md` is the internal spec — read it before changing code. This
README is the operator's guide: setup, deploy, run, observe.

## Quick start (local dev)

```bash
git clone https://github.com/8fqycwdt8v-oss/chemclaw2
cd chemclaw2
cp .env.example .env       # fill ANTHROPIC_API_KEY, OPENAI_API_KEY, CLERK_DOMAIN
docker compose up          # Postgres+pgvector, migrations, app, worker
curl localhost:8080/api/readiness
```

The compose file boots `pgvector/pgvector:pg16`, applies every file in
`migrations/` (mirroring CI), and starts the app + the fingerprint
worker. `/api/health` is liveness; `/api/readiness` adds DB + backlog
checks.

### Run without Docker

```bash
pip install -e ".[dev,chem]"             # core deps
pip install packages/mcp-servers/mcp_molfp \
            packages/mcp-servers/mcp_rxnfp \
            packages/mcp-servers/mcp_retrosynth \
            packages/mcp-servers/mcp_rxn_conditions \
            packages/mcp-servers/mcp_codesandbox \
            packages/mcp-servers/mcp_tabular
# Bring up your own Postgres on :5432 then:
for f in migrations/*.sql; do psql "$DATABASE_URL" --single-transaction -v ON_ERROR_STOP=1 -f "$f"; done
uvicorn api.main:app --reload --port 8080
```

## Testing

```bash
pytest api/                              # full suite (needs DATABASE_URL)
pytest api/ --cov=api                    # with coverage
pytest api/tests/test_health.py -v       # single file
ruff check api/ packages/
mypy api/
python -m tools.check_migrations         # migration policy
python -m tools.audit_allowlist          # SSRF allowlist sanity
```

Tests need a real Postgres because the queries layer exercises Postgres-
specific features (advisory locks, pgvector, FTS, JSONB). Start one with
`docker run -d -p 5432:5432 -e POSTGRES_PASSWORD=postgres pgvector/pgvector:pg16`.

## Architecture

```
HTTP request
   │
   ▼
RequestIdMiddleware  ─►  binds X-Request-ID to contextvars
   │
   ▼
CORSMiddleware       ─►  prod gate refuses empty / wildcard / localhost
   │
   ▼
FastAPI router       ─►  routes/ (chat, wiki, campaigns, audit, admin, …)
   │
   ▼
queries/             ─►  the only layer that imports SQLAlchemy primitives
   │
   ▼
Postgres + pgvector  ─►  state + sessions + wiki + audit + fingerprints

Agent runtime          ─►  api/agent/    ─►  Claude Agent SDK + MCP servers
Background workers     ─►  api/workers/  ─►  asyncio + Postgres advisory locks
```

- **No queue service** — workers poll Postgres with advisory locks.
- **No LLM proxy** — Claude Agent SDK + the Anthropic Python SDK direct.
- **No bespoke wiki engine** — Postgres FTS + pgvector embeddings.
- **No custom MCP client** — the SDK ships one.

`docs/` (added in Wave 7) carries the per-surface deep dives — auth flow,
rate limiting, agent hook lifecycle, MCP isolation tiers.

## Deployment

Production runs on Fly.io. `.github/workflows/deploy.yml` auto-deploys
on green CI to `main`. Secrets live in `fly secrets set` (never in
`.env.example` or git).

| Process | Command (in `fly.toml`)         | VM   | Replicas               |
|---------|---------------------------------|------|------------------------|
| web     | `uvicorn api.main:app --workers 2 …` | 1 GB | min 1, scale on traffic |
| worker  | `python -m api.workers.fp_worker`    | 512 MB | always 1                |

External health probe hits `/api/health` every 30s. Add a separate
`/api/readiness` probe in your platform of choice for drain semantics
(load balancers stop sending traffic; the process stays up).

Roll back via `fly deploy --image <previous-image-sha>`; Postgres has
point-in-time restore (see `docs/operations/backup-restore.md`).

## Observability

| Where                 | What                                                |
|-----------------------|-----------------------------------------------------|
| stdout (JSON)         | one line per request + per app event; `LOG_FORMAT=json` |
| `X-Request-ID` header | echoed back; correlates client and server logs      |
| `/api/health`         | liveness — process up, no DB                        |
| `/api/readiness`      | DB ping + fingerprint backlog gate (degraded > 5 k) |
| `/api/admin/health`   | admin-only deeper diagnostic (queue depths, etc.)   |

Errors swallowed by an `except` block always log (CLAUDE.md observability
rule). Rate-limit denials and SSRF blocks log at WARNING. Auth failures
log at WARNING. See `docs/architecture/rate-limiting.md` for the bucket
naming conventions.

## Environment variables

See `.env.example` — every key documented inline. Required for any
deploy: `DATABASE_URL`, `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`,
`CLERK_DOMAIN`, `CLERK_ISSUER`, `CLERK_JWKS_URL`, `ENV`, `ADMIN_USER_IDS`.

## Migrations

Plain SQL in `migrations/`. Policy in `migrations/MIGRATIONS.md`. CI
applies them in alphanumeric order with `psql --single-transaction`; the
`tools/check_migrations.py` linter runs first.

Before adding a migration:

```bash
git fetch origin main && ls migrations/  # pick a slot > everything on main
python -m tools.check_migrations         # confirms the new file conforms
```

## Branch note

`typescript_old` is an archive of the pre-Python monorepo. Read-only —
never merge, rebase, or delete.

## Contributing

Read `CLAUDE.md` first. Every change ships via a PR that goes green in
CI before merging to `main`. Direct commits to `main` aren't accepted.
Code conventions, security rules, and observability requirements are
non-negotiable — they're listed in `CLAUDE.md` because that's where the
agent reads them too.

## License

MIT — see `LICENSE`.
