# BACKLOG

Append-only ledger of deferred work for chemclaw2 (Python / FastAPI).

> History note: prior entries tracked the pre-Python TypeScript monorepo (Drizzle, `apps/web/`, `packages/db`, `packages/agent-tools`, `workers/fp-worker`, Tiptap, Next.js App Router). That code was deleted in commit `2b3ab16` ("python/phase9: delete TypeScript frontend, DB layer, agent-tools, and worker"). Carry-over items that referenced those paths were dropped on 2026-05-18 — see git history for the previous file if a TS-era observation is needed for context. The `typescript_old` branch remains the authoritative archive.

## Implementation roadmap

Ordered batches, each sized to one PR. File paths and migration numbers reference the current Python tree.

### Tier A — Security & correctness quick wins

- **A1. Auth hardening** (`api/auth.py`)
  - Validate JWT `iss` claim against `CLERK_ISSUER` env var so tokens minted for a different tenant are rejected.
  - Promote `ADMIN_USER_IDS` parsing to a one-shot startup check; fail fast on misconfig (currently parsed per request and only warns).
  - Refuse to start when `ALLOW_MOCK_AUTH=1` is set in a non-dev env (`ENV != "dev"`).
- **A2. Single substance-gate choke-point** (`api/agent/runner.py`, `api/routes/chat.py`)
  - The gate fires once in `chat.py:94` and again at `runner.py:80`. Drop the `runner.py` call so the gate has one entry point.
- **A3. Rate-limit key sanitization** (`api/db/queries/rate_limit.py` + all call sites)
  - Sanitize `user_id` (replace `:` and any whitespace) when composing buckets like `wiki-read:{user_id}` and `chat:{user_id}`. A user id containing a colon would alias buckets.

### Tier B — Performance fixes

- **B1. Campaign worker N+1 → batched step load** (`api/workers/campaign_worker.py`, `api/db/queries/campaigns.py`)
  - Replace the `for campaign in campaigns: get_pending_campaign_steps(...)` loop with one `get_pending_steps_for_campaigns(campaign_ids)` query.
- **B2. Budget cap TOCTOU** (`api/db/queries/budgets.py`, `api/agent/hooks.py`)
  - The PreToolUse hook checks the cap then the PostToolUse hook increments; two concurrent tools can both pass the check. Either (a) take a Postgres advisory lock per `project_key` around the (check, run, increment) sequence, or (b) collapse to a single `INSERT … RETURNING` with `WHERE used + 1 <= cap` and let the row count signal a deny.
- **B3. SSE buffer cap** (`api/agent/runner.py`, `api/routes/chat.py`)
  - Cap individual `AssistantMessage.content` block size (default 1 MB) so tool-heavy sessions can't OOM the process. Defer per-token streaming until the SDK exposes a callback.

### Tier C — Refactor

- **C1. Split `api/db/queries/wiki.py`** (521 LOC → ~260 LOC × 2)
  - `wiki_read.py`: list / get / search / `get_wiki_page_at` / revisions / citations / semantic search.
  - `wiki_write.py`: `upsert_wiki_page` / `patch_wiki_page` / chunking / embed-fan-out.
  - Update the ~10 call sites in one pass — no re-export shim. `from api.db.queries.wiki_read import …` reads better than a compatibility layer, and one find-and-replace is cheaper than carrying the shim forever.
- **C2. Extract embeddings module** (new `api/embeddings.py`)
  - Move `embed_texts` and `_get_oai` out of `api/routes/wiki.py`. Callers today: `routes/wiki.py`, `agent/tools.py` (semantic wiki lookup), `workers/campaign_worker.py` (campaign-wiki upsert).

### Tier D — Tests (highest leverage; gates real-traffic readiness)

- **D1. Pytest harness against `DATABASE_URL`**
  - Today only `test_health.py` exists; phases 1-8 of the Python migration (feedback / todos / budgets / admin / campaigns / wiki revisions+subscriptions+contradictions / notifications / integrations / audit) have zero coverage.
  - Shape: `pytest-asyncio` + `httpx.AsyncClient`, conftest fixtures for `db_session` (transaction-rollback per test), `authed_user_id`, `admin_user_id` (via `ALLOW_MOCK_AUTH=1` + `mock:userid` bearer token), and entity factories.
  - First cohort of tests to write (in order):
    1. Wiki migration triggers (0020 / 0030 trigger gate, `wiki_pages_auto_version`, FTS tsvector population).
    2. `pg_rate_limit` boundary (exactly-at-limit, one-over, fail-closed on exception).
    3. `get_wiki_page_at` (`as_of` after last edit returns current row, `as_of` before any revision returns 404 or earliest with a warning — see E1).
    4. `upsert_wiki_page` content-hash skip (no re-embed when text unchanged).
    5. End-to-end: POST `/api/wiki` then GET `/api/wiki/{slug}` with citations.
    6. Campaign worker happy path: kick off, step completes, campaign wiki created.
    7. Substance gate: blocked prompt returns 403 with `override_available`; valid override records audit row.
- **D2. ICH deep links** (`api/agent/tools.py:_ICH_URLS`)
  - All 13 keys point at the same `quality-guidelines` index page; replace with per-guideline PDFs as URLs are confirmed.

### Tier E — Polish (defer until measured pain)

- **E1. `get_wiki_page_at` temporal-exact flag** (`api/db/queries/wiki.py:427`, `api/routes/wiki.py`)
  - When the response is a revision whose `updated_at` ≠ requested `as_of` exactly, or when the page predates `as_of` and the earliest revision is returned anyway, include `temporal_exact: false` + a `temporal_warning` string. Compliance §3.8 reproducibility needs this signal.
- **E2. ELN fetch path verification** (`api/agent/tools.py`)
  - Path is `{ELN_API_BASE_URL}/api/eln/experiments/{id}` (TS version used `{ELN_API_BASE_URL}/experiments/{id}`). Confirm against the real ELN contract before connecting an ELN.
- **E3. CAS regex bound** (`api/agent/hooks.py`)
  - Currently `\d{2,7}` prefix. Align with CAS registry growth past 9 999 999 (no current pressure).

### Tier F — Long-horizon / blocked (kept as reference, not on the work plan)

- **Multi-tenant RLS** — every policy still `USING(true)`; migration 0034 dropped the truly permissive stubs but the remaining ones need per-tenant `USING (org_id = current_setting('app.org_id')::uuid)` bodies. Trigger: tenants > 1.
- **Wiki audit-read** — read-side audit table for compliance §3.8. Trigger: regulated customer asks. Write side already covered via revisions.
- **RLS on `notifications`** — enable with per-user predicate. Trigger: tenants > 1.
- **Skills catalog in DB** — promote filesystem skill packs to a table with scope (personal/project/org) + maturity tier. Trigger: skill count grows.
- **Tool forging** — NL tool synthesis with sandboxed execution (§3.13). v3 only.
- **Hybrid FTS + semantic with RRF fusion for wiki** — single mode today. Trigger: measurable recall failure when SMILES and paraphrase queries both miss.
- **`papers` / `properties` extraction pipeline** — tables exist (migrations 0026 / 0027) but nothing writes to them after the entity-extractor sub-agent was removed. Right shape is a post-`wiki_upsert` pg-boss job, deferred until demand.
- **LLM-level eval against a golden chemistry Q&A set** — `eval_runs` was scoped to deterministic probes only; LLM scoring deferred per §3.9 trigger (prompt iteration becomes the measured bottleneck).
- **Campaign approval UI** — `POST /api/campaigns/[id]/steps/[idx]/approve` exists but no UI. Build a campaign dashboard before adding interactive approval.
- **ORD export for reactions** — admin-only endpoint `/api/admin/reactions/export-ord` when an external partner asks for ORD interchange.

### Cleanup (small, low-risk)

- **Delete orphan TS packages** — `packages/agent-tools/` and `packages/db/` contain only stale `node_modules` and turbo logs. `packages/observability/` is a TypeScript package not imported by any Python file. Verify (`rg '@chemclaw2/observability'` returns no Python hits) then `rm -rf` and drop them from any workspace config. Keep `packages/mcp-servers/` (Python MCP servers `mcp_molfp` / `mcp_rxnfp`).

## Open observations (Python-era, not yet tiered)

- `api/db/pool` — `client.ts`-era note about `DB_POOL_MAX=15` referred to the deleted TS pool. Verify the Python equivalent in `api/db/connection.py` matches the Wave-3h fan-out reality (`lookup_knowledge` 5-way fan-out) and document upstream `max_connections` headroom if running without a pooler.
- `api/agent/tools.py` `lookup_regulatory_guidance` caches results in `external_facts` for 24 h. Cache invalidation when ICH publishes an update is manual; add a TTL warning in the response when the cache entry is older than 30 days.
- The substance gate fires once per turn but cannot detect session-level context where an attacker front-loads scheduled-substance context across earlier turns and asks for synthesis later. Mitigation requires a session-context scan; architectural, defer.
- Migration numbering: there are two `0029_*` files (`0029_tool_perm_check_and_eval_runs.sql` and `0029_wiki_tables_cleanup.sql`). Confirm both ran in production and re-number the second to 0029a or shift one to 0030.5; either way, document the chosen scheme to prevent CI collisions.
