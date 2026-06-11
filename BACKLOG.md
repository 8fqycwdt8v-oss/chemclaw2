# BACKLOG

Append-only ledger of deferred work for chemclaw2 (Python / FastAPI).

> History note: prior entries tracked the pre-Python TypeScript monorepo (Drizzle, `apps/web/`, `packages/db`, `packages/agent-tools`, `workers/fp-worker`, Tiptap, Next.js App Router). That code was deleted in commit `2b3ab16` ("python/phase9: delete TypeScript frontend, DB layer, agent-tools, and worker"). Carry-over items that referenced those paths were dropped on 2026-05-18 — see git history for the previous file if a TS-era observation is needed for context. The `typescript_old` branch remains the authoritative archive.

> **Prioritization + cleanup pass (2026-05-31).** Verified every open item against the codebase and removed those confirmed shipped: orphan TS packages (`packages/{agent-tools,db,observability}/` are gone — only `mcp-servers/` remains), route-layer integration tests (`test_routes_{admin,chat,wiki,campaigns}.py` exist), the tool-layer smoke harness (`test_tool_handlers_e2e.py` + the `build_*_tools` extraction / `tool_adapter.wrap_tool` resolved the SDK-access blocker), the `mcp_molfp`/`mcp_rxnfp` wheel layout (both now use `<name>/<name>/server.py`), and the CI mypy gate (re-enabled as a hard gate in `ci.yml`). All previously-struck-through ("shipped") sub-bullets were folded into the Shipped section below; full per-PR history is in git. Remaining open work is ranked P1 → P3 under **Open**.

## Shipped

### Tiers A–E (May 2026)

- **A — security & correctness quick wins** (PR #89, `1f6dec2`): JWT `iss` validation, startup `ADMIN_USER_IDS` parse, refuse `ALLOW_MOCK_AUTH=1` outside dev envs, single substance-gate choke-point, rate-limit key sanitization across 41 call sites.
- **B — performance fixes** (PR #90, `ac88683`): batched campaign step queries (N+1 → 3 queries/cycle), atomic `try_consume_tool_call` (eliminates budget-cap TOCTOU), 1 MB SSE block cap.
- **C — refactor** (PR #91, `5dfa97a`): split `wiki.py` (521 LOC) into `wiki_read.py` + `wiki_write.py`, extracted `api/embeddings.py`.
- **D — pytest harness + codebase-wide CAST fix** (PR #92, `618cbde`): conftest with mock-auth + AsyncSession fixtures; 28 new tests. Surfaced a latent codebase-wide bug — SQLAlchemy 2.0's `text()` parser mis-handles `:name::type` cast syntax; rewrote all 57 occurrences across 14 files to `CAST(:name AS type)`.
- **E — polish** (PR #93, `9a406b8`): `get_wiki_page_at` returns `temporal_exact: bool` + `temporal_warning: str | None` for compliance §3.8 reproducibility.

### Deep-review / refactor sweep (PRs #170–#177, 2026-05-30)

- **#170** — extracted `_cache_is_fresh` / `_parse_cached_payload` into `tool_helpers` (CACTUS / ICH / AiZynth / citation tools).
- **#171** — centralised keyset-cursor + UUID validation into `api/routes/_validation.py`; search fingerprint check → Pydantic `Field(pattern=…)` (422); fixed `PATCH /api/wiki/{slug}/seen` leaking internal version.
- **#172** — wall-capped the BOFIRE GP fit in `propose_next_conditions`; logged `fp_worker`'s two silent rollback failures.
- **#173** — logged `mcp_codesandbox`'s five silent kill-cleanup excepts; removed dead `import os`.
- **#176** — fixed bofire stale API (`qLogExpectedImprovement` → `qLogEI`) and pinned `bofire[optimization]>=0.3.1,<0.4`; GP-path test runs the real torch fit.
- **#177** — consolidated `new_campaign` factory into `api/tests/_factories.py`; 15 new HTTP-layer tests for `audit.py` / `curator.py` / `feedback.py`.

### CI / infra (resolved)

- **Heavy CI lane green on `main`** (PR #174 + #176) — `mordredcommunity` added to `[opt]`; bofire `qLogEI` fix. (Gating flip + `@pytest.mark.heavy` marker still open — see P1.)
- **CI mypy gate** — re-enabled as a hard gate in `.github/workflows/ci.yml`; the earlier CI-vs-local drift was resolved.
- **Migration policy** — documented in `migrations/MIGRATIONS.md`; CLAUDE.md points at it. New index migrations on populated tables use `CREATE INDEX CONCURRENTLY` in single-statement files.
- **Orphan TS packages deleted** — `packages/{agent-tools,db,observability}/` removed; only Python `mcp-servers/` remains.

### Feature surface (resolved)

- **MCP server merge** — `mcp_chem_intel` extracts the dependency-light primitives from the forward/retro/eln sibling repos (`score_synthesizability`, `classify_reaction`, `expand_abbreviation`). Heavy/agentic parts deferred (see P3).
- **Wiki audit-read** — `GET /api/audit/wiki/{slug}` (admin-only): page metadata + full revision list.
- **Hybrid FTS + semantic search with RRF fusion** — `hybrid_search_wiki` (K=60), default for `wiki_lookup` + `GET /api/search`; FTS-only preserved as `mode=fts`.
- **Campaign step approval** — `POST /api/campaigns/{id}/steps/{idx}/approve` + `/reject` + `GET /api/campaigns/steps/awaiting-approval`; agent tool `confirm_synthesis_plan` accepts per-step `requires_approval`. UI deferred.
- **Curator inbox** — `GET /api/curator/inbox` aggregates needs-review pages, pending-approval steps, unresolved contradictions.
- **Document ingestion** — DOI detection + CrossRef metadata + wiki draft (PR 4a); LLM compound/citation extraction via Haiku (`?extract=full`, PR 4b).
- **Property predictions** — `compute_descriptors(smiles)` in `mcp_molfp` (deterministic RDKit descriptors + Lipinski).
- **Bayesian optimisation** — `propose_next_conditions` 3-stage dispatcher (heuristic → BOFIRE LHS → GP+qLogEI) behind `[opt]` (PR #122).
- **Container-isolated sandbox** — bwrap → `unshare -n` → `python -I` tiered (PR #125); figure capture via `code_executions.artifacts` JSONB (migration 0042, PR #124).
- **Paper RAG** — HNSW index on `paper_chunks.embedding` (migration 0041, PR #119); end-to-end hybrid-search test + `IllegalStateChangeError` fix (PR #120); chunk-size config + RCS OpenAI fallback.
- **External retrosynthesis** — AiZynthFinder behind `[retrosynth]`; `propose_retrosynthesis_deep` with 5-min wall cap + 30-day cache (PR #126).
- **SSRF DNS-rebinding TOCTOU** — `_fetch_validated` resolves DNS once, pins the IP, passes hostname via `Host` + `sni_hostname`; all three call sites migrated.
- **Secret patterns** — Stripe/JWT/SendGrid/Twilio/npm added to `_SECRET_PATTERNS`.
- **Test/refactor hygiene** — split `campaigns.py` → `campaign_steps.py`; `rate_limit()` dependency factory across 26 routes; `fingerprints.py` extraction; campaign-wiki retry + `backfill_missing_campaign_wikis` worker pass.
- **Tests** — route-layer integration tests (`test_routes_{admin,chat,wiki,campaigns}.py`) + tool-layer smoke harness (`test_tool_handlers_e2e.py`).
- **MCP wheel layout** — all seven servers now use the proper `<name>/<name>/server.py` packaging.

### P1/P2 cleanup pass (2026-05-31)

- **Shared MCP logging** — new `mcp_chemclaw_shared` package exports `configure_logging(component)` + `JsonFormatter(component)`; the seven servers import it instead of hand-rolling the ~30-line formatter each. Pattern-A servers (molfp/codesandbox/tabular/retrosynth) configure at module import; pattern-B (rxnfp/chem-intel/rxn-conditions) keep the lazy `configure_logging` call in `main()`. Installed ahead of the servers in the Dockerfile + CI + README install lists.
  - **Known tradeoff (review #181):** the seven servers import `mcp_chemclaw_shared` but deliberately do NOT declare it in their `pyproject.toml` `dependencies` — it's a local-path package, not on PyPI, so declaring it would make a standalone `pip install packages/mcp-servers/<server>` *fail at install* instead of working in the co-install. Every install path (Docker, CI, README) lists the shared package alongside the servers. **When the P3 standalone HTTP MCP services land, each must co-install `mcp_chemclaw_shared`** (or it ships as a real published dep / path dependency then).
- **AiZynth cache-key canonicalisation** — `_canonical_smiles` (RDKit, falls back to the stripped input without RDKit / on parse failure) now keys the `aizynth:` `external_facts` entry, so `CCO` and `OCC` share a cache hit.
- **`@pytest.mark.heavy` marker wired** — the three bofire/torch GP tests in `test_optimization.py` are tagged; the cheap CI lane runs `-m "not heavy"` (importorskip kept as a belt-and-suspenders). Heavy lane still runs the full suite. (Gating flip remains — see P1.)
- **ICH cache staleness advisory** — `get_external_fact_by_source_id` now also selects `first_seen`; `lookup_regulatory_guidance` adds a `stale_warning` to a cached response once the entry has been tracked >30 days (advisory only, not a cache bust), via the reusable `_staleness_warning` helper.
- **`isolate_reactions` fixture** — opt-in conftest fixture TRUNCATEs the reactions tables (CASCADE reaches only `reaction_outcomes` + `reaction_condition_predictions`) before `test_find_similar_reactions_includes_outcomes`, de-flaking it on a reused local DB.

### P3 — ELN ORD-validation rule pack (2026-05-31)

- **ORD-validation tools** — `validate_ord_reaction` / `validate_ord_compound` added to `mcp_chem_intel` (new `ord_validate.py`). Wrap ord-schema's own recursive validator (`validate_message`) rather than hand-rolling CMP/STR/STO/ORD rules — one Reaction validation covers compounds, identifiers/SMILES, and amounts/stoichiometry. Return `{valid, errors, warnings, …}`; malformed JSON comes back as `valid=False` with the parse error (never raises) so the ELN self-repair loop can iterate. `ord-schema` is behind a new `[ord]` extra (it pulls protobuf/pandas/pyarrow) — base server stays light, tools degrade to a clear "not installed" envelope when absent. Installed in the Docker image (`mcp_chem_intel[ord]`) and the heavy CI lane; tests (`test_mcp_ord_validate.py`) are `@pytest.mark.heavy`. Agent runner already routes `mcp_chem_intel`, so the tools are auto-exposed. (The free-text→ORD *skill* that consumes them is still open — see P3.)

## Open

Ranked by readiness-to-implement. P1 = actionable now, trigger met, low risk. P2 = deferred until a stated trigger or demand. P3 = long-horizon / blocked on externals.

### P1 — actionable now

- **Flip the heavy CI lane to gating.** It is green on `main` but still `continue-on-error: true`. Once it logs 5 consecutive green runs, drop the flag in `.github/workflows/ci.yml`. (The `@pytest.mark.heavy` marker + cheap-lane `-m "not heavy"` deselect shipped — see Shipped below; only the continue-on-error flip remains, and it is gated on the 5-green-run observation.)

### P2 — deferred until trigger / demand

- **`papers` / `properties` extraction pipeline.** Tables exist (migrations 0026/0027) but nothing writes to them since the entity-extractor sub-agent was removed. Right shape is a post-`wiki_upsert` pg-boss job. Trigger: demand.
- **conftest fixture-isolation — broader TRUNCATE-teardown.** A targeted opt-in `isolate_reactions` fixture now de-flakes the one known case (see Shipped). A general autouse TRUNCATE-teardown across all tables stays deferred (broad blast radius, slows the suite); add it only if a second cross-test state-leak flake surfaces.
- **LLM extraction of `reactions.conditions` free-text → JSON at registration.** Would let `find_neighbor_conditions` return structured payloads directly instead of free-text. Defer until measured.
- **ORD export for reactions** — admin-only `/api/admin/reactions/export-ord`. Trigger: an external partner asks for ORD interchange.
- **Parametrise the ~20 near-identical `*_requires_auth` / `*_invalid_body` route tests** into shared parametrized cases. Low value; explicitly left as-is (≈12-file churn for near-zero benefit — re-evaluated 2026-05-31, still deferred).
- **Phase D optimisation candidates** (post Tier 1–3, BOFIRE surface):
  - **§A multi-objective BO** — add `MoboStrategy` + `qLogExpectedHypervolumeImprovement` when a customer asks for Pareto fronts (dispatcher currently rejects multi-output specs with a clear error).
  - **§A mixture variables + `LinearInequalityConstraint`** — widen `ParameterSpec` + tool-layer validation (current spec is box-only).
  - **§A `OPENAI_RCS_MODEL` reasoning-model support** — detect o1/o3 and switch `max_tokens` → `max_completion_tokens`.
  - **§A output keys beyond `yield_pct`** — widen the `reaction_outcomes` schema (only `yield_pct` is structured today).
  - **§B per-call `network=True` opt-in for `run_code`** — bwrap `--unshare-all` drops network; let agents that need HTTP opt in via a new tool arg.

### P3 — long-horizon / blocked on externals

- **External meta-model MCP servers (forward / retro / eln).** Consume the heavy/agentic parts of the merged sibling repos as remote HTTP MCP services (the upstreams already mount `fastapi-mcp` at `/mcp`); the dependency-light primitives already shipped in `mcp_chem_intel`. Keep the in-tree rule-based tools as offline fallbacks.
  - **SCScore / RAscore** — need ONNX/torch weights; add behind a heavy extra or the remote service (only SAscore, pure RDKit, shipped).
  - **`predict_forward_reaction`** — Borda ensemble of Molecular Transformer / T5Chem / ReactionT5 / MEGAN / Chemformer / GraphRXN; wire `chemclaw2_forward` via `FORWARD_MCP_URL`.
  - **`predict_reaction_conditions`** — Parrot/ASKCOS/two-stage-DNN; route to the remote service when configured, keep `mcp_rxn_conditions.predict_conditions` as the always-on default.
  - **`retrosynthesis_single_step` / `_multi_step`** — ~40 docker/GPU backends; consume via `RETRO_MCP_URL`, keep `mcp_retrosynth.disconnect` as the offline fallback.
  - **ELN protocol → ORD structuring** — the ORD-validation rule pack shipped (see Shipped: `validate_ord_reaction` / `validate_ord_compound` in `mcp_chem_intel`). **Still open:** the chemclaw *skill* that drives the existing agent loop (free-text procedure → structured ORD `Reaction` → validate → self-repair) using those validators + `classify_reaction` / `expand_abbreviation` / `validate_smiles` / `compute_descriptors`. Do NOT vendor a second nested agent runtime — it's a skill over the existing loop.
- **Multi-tenant RLS.** RLS is OFF on every table (migrations 0034/0043 disabled the `USING(true)` stubs across all 24). Re-enable with real per-tenant `USING (org_id = current_setting('app.org_id')::uuid)` bodies + wire `SET LOCAL app.org_id` on every transaction (the `withUserContext` helper from 0021 was exported but never invoked). Trigger: tenants > 1.
- **RLS on `notifications`** — per-user predicate. Trigger: tenants > 1.
- **Skills catalog in DB** — promote filesystem skill packs to a table with scope (personal/project/org) + maturity tier. Trigger: skill count grows.
- **Tool forging** — NL tool synthesis with sandboxed execution (§3.13). v3 only.
- **LLM-level eval against a golden chemistry Q&A set** — `eval_runs` is deterministic-probes-only; LLM scoring deferred per §3.9 trigger (prompt iteration becomes the measured bottleneck).
- **Novelty check: open-web / Semantic Scholar reach** — `check_hypothesis_novelty` (api/agent/tools_investigation.py) currently scores prior art against the *indexed* knowledge base only (ingested paper chunks + wiki). The AI-Scientist paper (Nature s41586-026-10265-5) filters ideas against Semantic Scholar. Add a Semantic Scholar / web-search retrieval leg once the org's indexed corpus proves too sparse to catch rediscoveries. No current pressure.
- **Reviewer / critic budget cap** — `review_draft` (6 LLM calls) and `critique_figure` (1 vision call) are charged through the existing per-project `try_consume_tool_call` like any tool. If operators want a separate VLM/judge spend cap (these calls are the expensive part), split it out. Defer until measured.
- ~~`check_citations` batching~~ — done: one `embed_texts(...)` for all claims, one session for all lookups via the new `get_external_facts_by_ids`, retrieval done before the judge calls so no DB connection is held across LLM latency.
- **Audit read-then-`begin()` collisions across remaining tools/routes** — fixed `propose_hypothesis`, `world_model_add`, and `record_contradiction` (an ownership SELECT auto-begins a tx that the subsequent write's `async with db.begin()` collided with → "A transaction is already begun" → the tool returned `internal tool error`; never caught because tests only hit the early not-found return). Vetted guard: `if db.in_transaction(): await db.rollback()` after the read (see `wiki_write.upsert_wiki_page`). Sweep the rest of the tools/routes that read then call a `begin()`-managing query in the same session; consider a shared `close_read_tx(db)` helper in `api/db/queries/_helpers.py`.
- **ORD ingestion pipeline** — backfill `reactions` with structured `ReactionConditions` from the Open Reaction Database. Heavy ETL; defer until the registry is too sparse to ground new campaigns.
- **Parrot / Reacon self-host trial** — only if RXN4Chemistry quota/accuracy is measured as the bottleneck (heavy GPU deps).
- **E2. ELN fetch path verification** (`api/agent/tools.py`) — path is `{ELN_API_BASE_URL}/api/eln/experiments/{id}`; confirm against the real ELN contract. Blocked on customer.
- **E3. CAS regex bound** (`api/agent/hooks.py`) — currently `\d{2,7}` prefix. Align with CAS growth past 9 999 999. No current pressure.
- **D2. ICH deep links** (`api/agent/tools.py:_ICH_URLS`) — per-document PDFs change each revision; needs offline verification of ICH's URL scheme. All 13 keys point at stable category landing pages today.

## Open observations (Python-era)

- `api/db/queries/wiki_write.py:upsert_wiki_page` does a read (`SELECT content_text`) then an explicit `async with db.begin():`. SQLAlchemy 2.0 async auto-begins a tx on the SELECT, so the function rolls back the empty read-tx before the explicit begin. Same antipattern would silently break any new query function that reads then begins — prefer one `db.begin()` covering both, or commit/rollback between phases. (Small, safe fix candidate.)
- `api/db/queries/budgets.py:try_consume_tool_call` charges for failed tool runs (caps attempts, not just successes). Documented inline. If operator preference shifts to success-only, split reserve/commit across PreToolUse/PostToolUse.
- The substance gate fires once per turn but cannot detect session-level context where an attacker front-loads scheduled-substance context across earlier turns and asks for synthesis later. Mitigation requires a session-context scan; architectural, defer.
- `api/db/connection.py` pool: `pool_size=5, max_overflow=10` (= Wave-3h `DB_POOL_MAX=15` total). Document upstream Postgres `max_connections` headroom if running without a pooler at scale.

## CI quality — partial adoption (May 2026)

- **mypy strict adoption** — CI runs mypy non-strict (`check_untyped_defs = true`, `no_implicit_optional = true`). `api.agent.tools` / `.runner` / `.hooks` are excluded via per-module overrides because the `claude_agent_sdk` TypedDicts don't match the SDK's own example usage (~30 errors not ours to fix). Re-enable strict per module as each is cleaned. Goal: full strict in 4–6 PRs.
- **Ruff rule expansion** — CI runs `ruff check api/ packages/` with `select = ["E", "F", "W", "I", "UP", "B"]` at `line-length=120` (the earlier "E,F,W only" note is stale; `I`/`UP`/`B` have since landed). Next rule families in order of value: `SIM` (simplification), `RUF` (Ruff-specific lints).

## Code-review follow-ups (June 2026)

- **routes/pagination** — `list_subscriptions` (wiki.py), `list_todos`, `list_session_feedback`, and admin `list_tool_permissions` return unbounded result sets. Per-user/per-session in practice, so deferred until measured; add `limit` query params + LIMIT in the query layer if any table grows past a few thousand rows per scope.
- **routes/chat** — `session_id` and `override_justification` validators coerce invalid values to `None` instead of raising 422 (intentional, pinned by `test_chat_invalid_session_id_silently_dropped`; justification fails closed). Revisit the contract if UI users report confusing "provide a justification" loops after sending a too-short justification.
- **deploy** — pin the Docker base image to a patch version (`python:3.11.x-slim`) and add a post-deploy health smoke step to `deploy.yml` (poll `/api/health` after Fly deploy).
- **integrations/graph** — `acquire_token` rebuilds `ConfidentialClientApplication` per call, discarding MSAL's in-process token cache. Fine at one call per sync cycle; cache the app instance if a hot path ever appears.
- **integrations/ingest** — entity-extraction PubChem lookups run up to 20 concurrent; add an `asyncio.Semaphore` if PubChem throttling is observed.
- **deps/rdkit** — `rdkit==2026.3.3` ships broken bundled type stubs (`rdkit-stubs/Chem/rdchem.pyi:347` non-default arg after default) that hard-fail mypy as a syntax blocker; excluded via `!=2026.3.3` in all four pyprojects. Drop the exclusion once an rdkit release with fixed stubs lands.
- **deps/bofire-botorch** — heavy CI lane fails since ~June 2026: unpinned `[opt]` resolves a new botorch that dropped the top-level `fit_fully_bayesian_model_nuts` export which `bofire 0.3.x` (`surrogates/fully_bayesian.py:6`) still imports → 2 failures in `test_optimization.py` (`test_propose_via_bofire_lhs_*`). Unrelated to app code; lane is non-blocking. Fix: cap botorch to the last version with the top-level export (or bump bofire when it supports new botorch), verify in the heavy lane, then resume the 5-green-runs gating plan.
