# BACKLOG

Append-only ledger of deferred work for chemclaw2 (Python / FastAPI).

> History note: prior entries tracked the pre-Python TypeScript monorepo (Drizzle, `apps/web/`, `packages/db`, `packages/agent-tools`, `workers/fp-worker`, Tiptap, Next.js App Router). That code was deleted in commit `2b3ab16` ("python/phase9: delete TypeScript frontend, DB layer, agent-tools, and worker"). Carry-over items that referenced those paths were dropped on 2026-05-18 — see git history for the previous file if a TS-era observation is needed for context. The `typescript_old` branch remains the authoritative archive.

## Shipped (Tiers A–E, May 2026)

Tiers A–E from the original roadmap were implemented in one batched session:

- **A — security & correctness quick wins** (PR #89, `1f6dec2`): JWT `iss` validation, startup `ADMIN_USER_IDS` parse, refuse `ALLOW_MOCK_AUTH=1` outside dev envs, single substance-gate choke-point, rate-limit key sanitization across 41 call sites.
- **B — performance fixes** (PR #90, `ac88683`): batched campaign step queries (N+1 → 3 queries/cycle), atomic `try_consume_tool_call` (eliminates budget-cap TOCTOU), 1 MB SSE block cap.
- **C — refactor** (PR #91, `5dfa97a`): split `wiki.py` (521 LOC) into `wiki_read.py` + `wiki_write.py`, extracted `api/embeddings.py`.
- **D — pytest harness + codebase-wide CAST fix** (PR #92, `618cbde`): conftest with mock-auth + AsyncSession fixtures; 28 new tests covering rate-limit / substance gate / wiki queries / budgets / batched campaigns. Surfaced a latent codebase-wide bug — SQLAlchemy 2.0's `text()` parser mis-handles `:name::type` cast syntax (consumes one char off the bind name), leaving literal `:cid::uuid` in the SQL. Rewrote all 57 occurrences across 14 files to `CAST(:name AS type)`. Production wiki upserts have presumably been 500'ing on every call since the Python port; no test caught it because none exercised a write-path query before this PR.
- **E — polish** (PR #93, `9a406b8`): `get_wiki_page_at` now returns `temporal_exact: bool` + `temporal_warning: str | None` so compliance §3.8 reproducibility can distinguish exact vs best-effort bi-temporal results.

## Open

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
- **E2. ELN fetch path verification** (`api/agent/tools.py`) — path is `{ELN_API_BASE_URL}/api/eln/experiments/{id}`; confirm against the real ELN contract before connecting an ELN. Blocked on customer.
- **E3. CAS regex bound** (`api/agent/hooks.py`) — currently `\d{2,7}` prefix. Align with CAS registry growth past 9 999 999. No current pressure.
- **D2. ICH deep links** (`api/agent/tools.py:_ICH_URLS`) — per-document PDFs change with each revision; needs offline verification of ICH's current URL scheme before any deep links can be added safely. Today all 13 keys point at the stable category landing pages, which combined with the 24h `external_facts` cache and the `topic` filter does the substantive work.

### Cleanup (small, low-risk)

- **Delete orphan TS packages** — `packages/agent-tools/` and `packages/db/` contain only stale `node_modules` and turbo logs. `packages/observability/` is a TypeScript package not imported by any Python file. Verify (`rg "@chemclaw2/observability"` returns no Python hits, no Python uses its exports) then `rm -rf` and drop them from any workspace config. Keep `packages/mcp-servers/` (Python MCP servers `mcp_molfp` / `mcp_rxnfp`).

## Open observations (Python-era)

- `api/db/queries/budgets.py:try_consume_tool_call` now charges for failed tool runs (cap budgets attempts, not just successes). Documented inline. If operator preference shifts to success-only counting, separate reserve/commit on PreToolUse/PostToolUse.
- `api/db/queries/wiki_write.py:upsert_wiki_page` does a read (`SELECT content_text`) followed by an explicit `async with db.begin():`. SQLAlchemy 2.0 async auto-begins a tx on the SELECT, so the function rolls back the empty read-tx before the explicit begin. Same antipattern would silently break in any new query function that reads then begins — prefer one `db.begin()` block covering both, OR commit/rollback between phases.
- `api/agent/tools.py` `lookup_regulatory_guidance` caches results in `external_facts` for 24 h. Cache invalidation when ICH publishes an update is manual; add a TTL warning in the response when the cache entry is older than 30 days.
- The substance gate fires once per turn but cannot detect session-level context where an attacker front-loads scheduled-substance context across earlier turns and asks for synthesis later. Mitigation requires a session-context scan; architectural, defer.
- Migration numbering: there are two `0029_*` files (`0029_tool_perm_check_and_eval_runs.sql` and `0029_wiki_tables_cleanup.sql`). Confirm both ran in production and re-number the second to 0029a or shift one to 0030.5; either way, document the chosen scheme to prevent CI collisions.
- `api/db/connection.py` pool: `pool_size=5, max_overflow=10` is the Python equivalent of the Wave-3h `DB_POOL_MAX=15` total. Document upstream Postgres `max_connections` headroom if running without a pooler at scale.
