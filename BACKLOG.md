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
- ~~Wiki audit-read~~ — shipped in feat/wiki-audit-read PR. `GET /api/audit/wiki/{slug}` returns the current page metadata (version, created_by/updated_by, bi-temporal valid_from/valid_to, maturity, archived, needs_review) bundled with the full revision list ordered newest-first. Admin-only (uses `_AUDIT_WIKI` deps — admin check before rate-limit so non-admins still see 403 not 429). Pairs with the existing `GET /api/wiki/{slug}/revisions/{version}` for full revision bodies when a diff is needed.
- **RLS on `notifications`** — enable with per-user predicate. Trigger: tenants > 1.
- **Skills catalog in DB** — promote filesystem skill packs to a table with scope (personal/project/org) + maturity tier. Trigger: skill count grows.
- **Tool forging** — NL tool synthesis with sandboxed execution (§3.13). v3 only.
- ~~Hybrid FTS + semantic with RRF fusion for wiki~~ — shipped in feat/wiki-hybrid-search PR. New `hybrid_search_wiki` runs both legs in parallel and fuses via RRF (K=60). Default mode for the `wiki_lookup` agent tool and `GET /api/search` (the previous FTS-only path is preserved as `mode=fts` for exact-term queries like SMILES/CAS).
- **`papers` / `properties` extraction pipeline** — tables exist (migrations 0026 / 0027) but nothing writes to them after the entity-extractor sub-agent was removed. Right shape is a post-`wiki_upsert` pg-boss job, deferred until demand.
- **LLM-level eval against a golden chemistry Q&A set** — `eval_runs` was scoped to deterministic probes only; LLM scoring deferred per §3.9 trigger (prompt iteration becomes the measured bottleneck).
- ~~Campaign approval API~~ — shipped in feat/campaign-step-approval PR. Backend `POST /api/campaigns/{id}/steps/{idx}/approve` + `/reject` + `GET /api/campaigns/steps/awaiting-approval` with owner-scope + source-state predicate. Agent tool `confirm_synthesis_plan` now accepts `requires_approval: bool` per step. UI surface deferred.
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
- ~~Migration numbering: there are two `0029_*` files~~ — resolved by renaming `0029_wiki_tables_cleanup.sql` → `0029a_wiki_tables_cleanup.sql` in the review-fixes-A PR.
- `api/db/connection.py` pool: `pool_size=5, max_overflow=10` is the Python equivalent of the Wave-3h `DB_POOL_MAX=15` total. Document upstream Postgres `max_connections` headroom if running without a pooler at scale.

### Curator inbox (May 2026 — V3 PR 1)

- ~~Curator queue / inbox~~ — shipped. `GET /api/curator/inbox` aggregates wiki pages with `needs_review=true` (owner-scoped), campaign steps in `pending_approval` (owner-scoped), and unresolved wiki contradictions (collaborative) into a single response with `total_pending` count. New query `list_wiki_needs_review(db, user_id, limit)` in `api/db/queries/wiki_read.py`. New router `api/routes/curator.py` registered in `main.py`.

### Document ingestion LLM extraction (May 2026 — V2 PR 4b)

- ~~Document ingestion: LLM-driven compound + citation extraction~~ — shipped. `extract_entities_from_text(text)` calls Claude Haiku 4.5 with a structured tool-use schema (`extract_entities` forced via `tool_choice`) to pull compound mentions + citation identifiers from the document body. Input bounded to 8 K chars; total budget 20 s timeout. `resolve_compound_name_to_smiles(name)` follows up with PubChem REST (already on `ALLOWED_DOMAINS`) via `_fetch_validated`. Enrichment is opt-in via `POST /api/integrations/documents?extract=full` — `basic` (default) keeps the PR 4a behaviour for cheap/fast uploads. The wiki page draft now includes auto-extracted compound + citation sections; `external_facts.payload` carries the raw entities + resolved SMILES for later reuse.

### Document ingestion baseline (May 2026 — V2 PR 4a)

- ~~Document ingestion: DOI detection + CrossRef metadata + wiki page draft~~ — shipped. `api/integrations/document_enrichment.py` houses `extract_doi`, `slugify_doi`, `fetch_crossref_metadata`, `normalize_crossref_response`. The `/api/integrations/documents` route now (a) detects a DOI in the extracted PDF text, (b) fetches CrossRef metadata when one is present (via the existing SSRF-pinned `_fetch_validated` — `crossref.org` already on the allowlist), (c) enriches the `upsert_paper` call with abstract + content_text, and (d) creates a wiki page draft with `needs_review=True` so the curator queue surfaces it. CrossRef calls fail-open: a network blip or 404 falls through to the first-non-empty-line title heuristic so the upload still succeeds.

### Property predictions (May 2026 — V2 PR 3)

- ~~Spec §3.5 property predictions (deterministic descriptors)~~ — shipped `compute_descriptors(smiles)` in `mcp_molfp`: Crippen logP, exact/avg MW, TPSA, H-bond donors/acceptors, rotatable bonds, aromatic rings, heavy atoms, Lipinski Rule-of-Five pass + violation count. All values come from RDKit (no ML, no external calls, deterministic). The agent SDK auto-discovers it via the existing `mcp-molfp` stdio routing in `api/agent/runner.py`. ML-based predictions (yield, tox, hazards) remain deferred per the operating principles.

### Migration policy (May 2026 — review-fix PR F)

- ~~46 `CREATE INDEX` statements lack `CONCURRENTLY`~~ — policy now documented in `migrations/MIGRATIONS.md`. Historical migrations 0001–0036 are already applied; rewriting them retroactively has no effect. New index migrations on populated tables must use `CREATE INDEX CONCURRENTLY` and live in a single-statement file (CI's `--single-transaction` apply forbids mixing `CONCURRENTLY` with other DDL). CLAUDE.md updated to point at the policy file.

### CI quality — partial adoption (May 2026)

- **mypy strict adoption** — CI now runs mypy in *non-strict* mode (config: `strict = false`, `check_untyped_defs = true`, `no_implicit_optional = true`). `api.agent.tools`, `api.agent.runner`, and `api.agent.hooks` are excluded via per-module overrides because the `claude_agent_sdk` TypedDicts (`McpSdkServerConfig`, `AgentDefinition`, `HookMatcher`) don't match how the SDK's own examples use them, producing ~30 errors that aren't ours to fix. Re-enable strict mode per module as each is cleaned: drop the override, fix any errors that surface. Goal: full strict in 4-6 more PRs.
- **Ruff rule expansion** — CI runs `ruff check --select=E,F,W` at `line-length=120`. Auto-fix pass cleaned 42 issues (unused imports + isort) and 4 wrapped lines. Next rule families to add (in order of value): `I` (sorted imports — cosmetic but standardising), `UP` (pyupgrade — auto-rewrites old syntax), `B` (bugbear — catches real bugs), `SIM` (simplification suggestions), `RUF` (Ruff-specific lints).

### Campaign wiki retry (May 2026 — review-fix PR E)

- ~~`_create_campaign_wiki` outside completion transaction (no retry)~~ — resolved. The function now retries with exponential backoff (0/1/2/4 s, four attempts total) and returns `{"ok": bool, "error": str | None}` so the caller can react. The completion transaction is intentionally kept inline-only (the slow embed call would otherwise hold the txn open). A new `backfill_missing_campaign_wikis` worker pass runs every 5 cycles (≈ 5 min at the default 60 s interval) and re-attempts wiki creation for completed campaigns from the last 24 h whose `campaign-{id}` wiki slug doesn't exist. The 24 h cap stops the worker from endlessly retrying historically-failed campaigns; operators backfill anything older by fixing the underlying cause (embedding API outage etc.) and running the worker tick manually.

### Deferred from test-coverage / audit pass (May 2026)

- ~~SSRF: DNS-rebinding TOCTOU~~ — resolved in fix/ssrf-dns-rebinding PR. `_fetch_validated` resolves DNS exactly once, rewrites the URL to use the resolved IP for the actual connection, and passes the original hostname via `Host` header + httpcore `sni_hostname` extension (for TLS SNI + certificate verification). All three call sites (`fetch_document`, `lookup_regulatory_guidance`, `eln_fetch_experiment`) migrated to the shared helper.
- ~~Stripe pattern in `_SECRET_PATTERNS`~~ — resolved in the review-fixes-A PR. Also added JWT, SendGrid, Twilio SID, npm token patterns.
- ~~Split `api/db/queries/campaigns.py`~~ — resolved in refactor/ratelimit-dep PR. Step-level functions live in `campaign_steps.py`; `campaigns.py` re-exports them for back-compat. Both files <240 LOC.
- ~~Extract fetch-with-redirect helper~~ — resolved by `_fetch_validated` in the DNS-rebinding fix PR. The redirect loop is now in exactly one place and applies the IP-pinning + allowlist-revalidate cycle uniformly across all three call sites.
- ~~Extract rate-limit dependency~~ — resolved in refactor/ratelimit-dep PR. `rate_limit(bucket, n, window_ms=60_000, *, optional_user=False)` is a FastAPI dependency factory in `api/db/queries/rate_limit.py`. Migrated 26 routes across wiki, campaigns, admin, audit, todos, notifications, search, feedback, budgets, integrations. Two call sites kept inline: `routes/chat.py` (custom SSE error response) and the `eln-webhook` global bucket in `routes/integrations.py`. Wiki ownership-check duplication still present (3 sites) — separate follow-up.
- ~~`patch_wiki_page` defense-in-depth~~ — resolved in review-fixes-A: UPDATE now includes `AND created_by = :updated_by`.
- ~~`fp_worker` imports `sqlalchemy.text`~~ — resolved in review-fixes-A: extracted into `api/db/queries/fingerprints.py`.
- **Route-layer integration tests** — `routes/wiki.py`, `routes/admin.py`, `routes/chat.py` SSE happy path and `routes/campaigns.py` are not exercised end-to-end. Health smoke + substance gate are the only HTTP-layer tests today.

### Phase A follow-ups (paper RAG / retrosynth / ChemCrow, May 2026)

- **HNSW index on `paper_chunks.embedding`** — deferred per migration 0038 comment; build with `CREATE INDEX CONCURRENTLY` once row counts justify it (matches the wiki_chunks pattern, which still does linear scans). Trigger: > 10k ingested chunks across active papers.
- **DB-integration test for `paper_qa` end-to-end** — `test_paper_chunks.py` covers chunking only. A `test_papers_hybrid_search.py` modeled on `test_hybrid_search.py` would seed chunks, embed via the test conftest's stubbed embedder, run `hybrid_search_paper_chunks`, and assert RRF ordering. Blocked on a test-side embedder stub usable from a query-layer test.
- **MCP-tool smoke tests for the new chemistry surface** — `paper_qa`, `propose_retrosynthesis`, `name_to_structure`, `patent_coverage` are exercised only via their underlying libraries today. Mirror the SSRF-rejection pattern from `test_ssrf.py` for each.
- **Chunk-size / overlap config** — `_ingest_paper_chunks` hardcodes 1500/200. Surface via `PAPER_CHUNK_SIZE` / `PAPER_CHUNK_OVERLAP` env vars when a customer pushes back on the defaults.
- **OpenAI fallback for RCS scoring** — `score_chunks_with_llm` is Anthropic-only. Add an OpenAI path keyed off `RCS_PROVIDER=openai` if a future deployment can't depend on `ANTHROPIC_API_KEY`.
- **External retrosynthesis service** — the 11-template curated library is a deliberate floor. When demand justifies, wire AiZynthFinder / ASKCOS / IBM RXN as a second `propose_retrosynthesis_deep` tool, keeping the template fast-path for first-pass disconnections.

### Reaction condition prediction (May 2026)

- **mcp_molfp / mcp_rxnfp wheel layout** — both servers declare `packages = ["<name>"]` in `pyproject.toml` but place `__init__.py` + `server.py` at the project root, not in a `<name>/` subdirectory. Hatchling silently builds wheels containing only metadata; `python -m mcp_<name>.server` would fail on import after `pip install` unless PYTHONPATH happens to point at `packages/mcp-servers/`. The new `mcp_rxn_conditions` server uses the proper `<name>/<name>/server.py` layout. Fix the two existing servers in a follow-up by moving their `.py` files into a subdirectory and updating the Dockerfile / CI install paths.
- **Shared `JsonFormatter` / `_configure_logging` util across MCP servers** — third copy now lives in `mcp_rxn_conditions/server.py`. Extract to a small `mcp_chemclaw_shared` package when a fourth server is added.
- **LLM extraction of `reactions.conditions` free-text → JSON** — Phase A's `suggest_conditions_from_neighbors` returns free-text from historical reactions; an LLM extractor at registration time would let `find_neighbor_conditions` return structured payloads directly. Defer until measured.
- **ORD ingestion pipeline** — backfill `reactions` with structured `ReactionConditions` from the Open Reaction Database. Useful precedent for the neighbor lookup, but heavy ETL work — defer until the registry is too sparse to ground new campaigns.
- **Parrot / Reacon self-host trial** — only if RXN4Chemistry quota / accuracy is measured as the bottleneck. Heavy deps (PyTorch + transformers); requires GPU container. Today the hosted SDK is a one-line `RXN4Chemistry` dep, no custom inference infra.
