# Phase A / B / C follow-up concepts

> **Status (June 2026):** historical concept doc. The BO (§A) and deep
> retrosynthesis concepts shipped and were later **removed** in the
> lean pass (PR #189). Treat sections here as design history, not a
> description of the current system.

Concept-level designs for every open BACKLOG item produced during the Kosmos-style scaffolding work (PRs #114, #116, #117). One section per item: **Goal → Approach → Key decisions → Trade-offs → Effort.** This is *concept*, not implementation — each section is sized for a single follow-up PR.

The Bayesian-optimisation item (§A) uses **BOFIRE** as the backing library per the operating-principles directive ("off-the-shelf over self-built"). BOFIRE is BASF's open framework for chemical-process BO, MIT-licensed, BoTorch under the hood, with first-class support for the mixed-categorical-continuous-mixture parameter spaces real campaigns actually need.

## Sequencing

Tiers below are dependency-aware: tier-1 items unblock tier-2/3 and have no external deps. Within a tier, order is by leverage.

**Size legend.** XS ≤ 50 LOC · S ≤ 200 LOC · M ≤ 500 LOC · L ≤ 1500 LOC.

| Tier | Item | Rough size | Notes |
|---|---|---|---|
| 1 | **§J** mypy CI gate re-enablement | XS | Required to land tier-2/3 with confidence |
| 1 | **§I** `_probe_unshare` lazy | XS | One decorator |
| 1 | **§K** Process-leak fix on non-Timeout sandbox exceptions | XS | 5 LOC |
| 1 | **§F** Chunk-size / overlap config via env vars | XS | Two `os.environ.get` reads |
| 1 | **§C** HNSW index on `paper_chunks.embedding` | S | Triggered by row count |
| 2 | **§D** End-to-end DB test for `paper_qa` | S | Stubbed embedder + RCS |
| 2 | **§E** Tool-layer smoke tests across Phases A/B/C | M | ~35 tools |
| 2 | **§L** `code_executions` query-layer ownership validation | S | Single EXISTS check |
| 2 | **§G** OpenAI fallback for RCS scoring | S | `RCS_PROVIDER` env switch |
| 3 | **§A** Real BO via **BOFIRE** | L | New extras, schema delta |
| 3 | **§B** Container-isolated sandbox (bubblewrap) | L | Deployment touchpoint |
| 3 | **§H** External retrosynthesis service (AiZynthFinder) | L | ~4 GB model files |
| 3 | **§M** Figure capture from sandbox | M | Schema delta + matplotlib glue |

---

## A. Real Bayesian optimisation via BOFIRE

### Goal

Replace the V1 heuristic `propose_next_conditions` with a proper BO loop that learns a surrogate over (conditions → outcome) and proposes the next experiment via an acquisition function — single- or multi-objective.

### Approach

**Library:** [BOFIRE](https://github.com/experimental-design/bofire). BASF's chemical-process BO framework. MIT-licensed, actively maintained, BoTorch + GPyTorch under the hood. Supports continuous + discrete + categorical + mixture inputs in one Domain, multi-objective Pareto, hard constraints. Off-the-shelf — exactly the shape CLAUDE.md's operating-principle #1 asks for.

**Integration shape:**

1. **Parameter-space declaration.** Campaigns currently store `plan` as opaque JSONB. Add a structured *parameter spec* sub-schema:
   ```json
   {
     "inputs": [
       {"key": "temperature", "type": "continuous", "min": 20, "max": 120, "unit": "C"},
       {"key": "solvent", "type": "categorical", "categories": ["THF", "DMF", "EtOH"]},
       {"key": "catalyst_loading", "type": "continuous", "min": 0.5, "max": 10.0, "unit": "mol%"}
     ],
     "outputs": [
       {"key": "yield", "type": "continuous", "direction": "maximize"},
       {"key": "purity", "type": "continuous", "direction": "maximize", "min_constraint": 0.95}
     ]
   }
   ```
   Stored inside `synthesis_campaigns.plan.parameter_spec`. No schema migration needed — JSONB already.

2. **New tool `declare_campaign_parameter_space(campaign_id, spec)`** at the agent layer. The agent or user calls this once before running BO. Owner-scoped.

3. **Canonical outcome feed: `reaction_outcomes`, not `campaign_steps.result`.** PR #115 added a structured `reaction_outcomes` table (`yield_pct`, `purity_pct`, etc.); `campaign_steps.result` JSONB pre-dates it and stays as the agent's free-form scratch. BOFIRE reads from `reaction_outcomes JOIN campaign_steps` so its inputs are typed at the schema layer — no JSONB shape-guessing.

4. **`propose_next_conditions` rewrite.** New module `api/db/queries/optimization.py` with two paths:
   - When `parameter_spec` is absent → fall back to the current V1 heuristic (preserves backwards compatibility).
   - When `parameter_spec` is present → BOFIRE path:
     ```python
     domain = bofire_domain_from_spec(parameter_spec)
     # Build experiments DataFrame from reaction_outcomes JOIN campaign_steps,
     # one row per completed step with all declared outputs observed.
     experiments = experiments_dataframe(completed_outcomes, domain)
     strategy = SoboStrategy(domain=domain, acquisition_function=qLogExpectedImprovement())
     strategy.tell(experiments)
     proposals = strategy.ask(candidate_count=n_proposals)
     ```
   - Map BOFIRE `Experiment` rows back to chemclaw2's `conditions` dict format and return.

5. **Partial-observation handling.** When the parameter spec declares multiple outputs (yield + purity) but only one is observed in a given step, BOFIRE's GP fit fails. V1 requires all declared outputs to be observed per step; rows with NULLs are dropped before `strategy.tell()`. Imputation or multi-task GP that handles missing outputs is filed as §A.1 for a later follow-up.

6. **Parameter-spec UX.** §A's `parameter_spec` is structured JSON — non-technical users can't write it directly. V1 ships with two paths: (a) a power-user / agent JSON shape, (b) agent-mediated declaration where the chat agent gathers the variables conversationally and emits the JSON. A UI form-generator is future work, not blocking.

4. **Cold-start.** Fewer than 5 completed steps → use BOFIRE's `RandomStrategy` (Latin Hypercube) for diverse exploration. ≥5 → switch to `SoboStrategy` with GP surrogate.

### Key decisions

- **Surrogate:** `MixedSingleTaskGPSurrogate` — BOFIRE's default for mixed cat+cont, identical to BoTorch's `MixedSingleTaskGP`. Robust to small N (5–50 datapoints typical in early campaigns).
- **Acquisition:** `qLogExpectedImprovement` — numerically stable, BOFIRE's recommended default since v0.0.13.
- **Multi-objective:** Use `MoboStrategy` + `qLogExpectedHypervolumeImprovement` when ≥2 outputs declared. Returns Pareto-optimal proposals.
- **Constraints:** BOFIRE first-class — solubility, hazard, cost ceilings as `LinearInequalityConstraint` or `NonlinearInequalityConstraint`. Agent declares them as part of the parameter spec.

### Trade-offs

- **+BOFIRE → +BoTorch → +GPyTorch → +torch.** ~2 GB image growth. Mitigation: put under a new `[opt]` extras_require, not default — `pip install ".[opt]"` only on hosts that need BO. The agent tool detects ImportError and surfaces `{"error": "BOFIRE not installed"}` cleanly.
- **GP fit cost.** For >200 datapoints per campaign, GP becomes slow. Mitigation: subsample to most-recent 100 + best-100 by yield; document the limit.
- **Categorical encoding.** BOFIRE uses one-hot for cats — exponential parameter blowup at 5+ levels. Mitigation: cap categorical levels at 8 with a docstring warning.

### Effort

L. Schema-light but library-heavy. ~600 LOC across the new queries module, the BOFIRE Domain construction helpers, the agent tools, and tests. Plan: separate `[opt]` extras + the parameter-spec tool first as one PR; rewire `propose_next_conditions` as a second.

---

## B. Container-isolated sandbox

### Goal

Move `mcp_codesandbox` from subprocess + RLIMIT to a real container runtime so escape requires a kernel vuln rather than a Python escape.

### Approach

**Library candidates:**

| Tool | Daemon | Setuid | License | Profile |
|---|---|---|---|---|
| Docker | yes | no | Apache-2 | Heaviest; production-grade |
| firejail | no | **yes** | GPL-2 | Declarative profiles; setuid is a deployment concern |
| nsjail | no | no | Apache-2 | Google; namespace-based |
| **bubblewrap (`bwrap`)** | no | no | LGPL-2.1+ | Flatpak's sandbox; lightweight; ~50 KB binary |

**Recommended: bubblewrap.** No daemon, no setuid, ships in every recent Debian/Ubuntu/Fedora, and used by Flatpak in production at scale. Fits the chemclaw2 single-process Python deploy story.

**Integration shape:**

`mcp_codesandbox/sandbox.py:_build_command` gains a third branch:
```python
if _BWRAP_AVAILABLE:
    return [
        "bwrap",
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/lib", "/lib",
        "--ro-bind", "/lib64", "/lib64",
        "--ro-bind", "/etc/python3.11", "/etc/python3.11",
        "--tmpfs", "/tmp",
        "--tmpfs", "/home",
        "--proc", "/proc",
        "--dev", "/dev",
        "--unshare-all",
        "--die-with-parent",
        "--cap-drop", "ALL",
        sys.executable, "-I", "-c", code,
    ]
```

Probed at module load via `bwrap --version` exit code, same pattern as `_probe_unshare`. Falls back to current subprocess path on hosts where bwrap is unavailable.

### Key decisions

- **Read-only `/usr`** means the sandbox can `import numpy` etc. but can't modify the host's Python install.
- **Tmpfs `/tmp` and `/home`** means filesystem writes vanish on exit, matching current behaviour.
- **`--unshare-all`** drops every namespace (pid, net, ipc, uts, cgroup, mount, user) in one flag.
- **`--die-with-parent`** is the kill-switch: if the parent crashes, the child is reaped automatically.
- **`--cap-drop ALL`** ensures even setuid binaries inside the sandbox can't escalate.
- **Docker-in-Docker caveat.** `bwrap --proc /proc` typically fails inside a Docker container because the parent's `--security-opt no-new-privileges` and default seccomp profile block the unshare-based `/proc` mount. Two deployment models:
  - **Bare-metal / VM hosts** (Fly machines, EC2 instances): bwrap works as designed.
  - **Containerised hosts** (k8s, Cloud Run, Docker workers): rely on the container runtime's own isolation (Docker already gives us a fresh cgroup + cap set); bwrap adds no value and may fail. The sandbox detects this case via the bwrap probe and falls back to the current subprocess + RLIMIT path.

### Trade-offs

- **Slightly higher launch latency.** Bubblewrap adds ~20 ms over plain subprocess. Acceptable; sandbox runs are not a hot path.
- **Network drop is now real.** Today's `unshare -n` is best-effort; bwrap makes it the default. Agent code that legitimately needs HTTP must declare it; the tool can grow a `network=True` arg that emits a different argv.
- **Distro deps.** bubblewrap not present in slim images. Mitigation: add `bubblewrap` to the Dockerfile's apt install list; sandbox falls back to subprocess on hosts without it.

### Effort

L. ~200 LOC sandbox change + Dockerfile delta + a probe test. Plan: one PR.

---

## C. HNSW index on `paper_chunks.embedding`

### Goal

Drop semantic-search latency on `paper_chunks` from O(n) linear scan to O(log n) once row counts justify the build cost.

### Approach

```sql
-- migrations/0041_paper_chunks_hnsw.sql (its own file — CONCURRENTLY can't share a transaction)
CREATE INDEX CONCURRENTLY IF NOT EXISTS paper_chunks_embedding_hnsw_idx
    ON paper_chunks USING hnsw (embedding vector_cosine_ops);
```

### Key decisions

- **`vector_cosine_ops`** matches the cosine-distance query in `semantic_search_paper_chunks` (`<=>` operator).
- **CONCURRENTLY** so the build doesn't take ACCESS EXCLUSIVE on the table — paper ingest stays online.
- **Default `m=16, ef_construction=64`** initially. Tune only after measuring.

### Trade-offs

- **Build cost:** ~4 GB RAM + ~minutes for 100k chunks. Run during maintenance window.
- **Insert cost:** HNSW writes are ~5× slower than no-index. Paper ingest is bursty + offline so acceptable.

### Effort

XS. Single SQL file. Trigger: > 10k chunks across active papers.

---

## D. End-to-end DB test for `paper_qa`

### Goal

Lock down the chunking → embedding → hybrid retrieval → RCS reranking flow against the CI Postgres.

### Approach

New `api/tests/test_papers_hybrid_search.py` mirroring `test_hybrid_search.py`:

1. **Stub embedder** in conftest: `noop_embedder(texts) → [[1.0 if i == hash(t) % 1536 else 0.0 for i in range(1536)] for t in texts]` — deterministic, easily-distinguishable vectors per text.
2. **Stub RCS** scorer: returns score = `len(chunk) % 10 + 1`, summary = chunk's first 80 chars. Deterministic.
3. **Test cases:**
   - Insert 5 papers × 3 chunks each, run `hybrid_search_paper_chunks("query"), assert RRF ordering.
   - Insert papers in two ownership scopes, assert owner-scoping doesn't leak.
   - Insert a chunk with `embedding=NULL`, assert it's returned by FTS-leg but not semantic-leg.
   - Empty paper_chunks table → returns `[]` cleanly.

### Key decisions

- **Stubbed embedder, not real OpenAI** — CI shouldn't depend on external APIs for unit tests.
- **Real Postgres + pgvector** — the SQL itself is what's being tested.

### Trade-offs

None. Cheap to add, high value.

### Effort

S. ~150 LOC.

---

## E. Tool-layer smoke tests across Phases A/B/C

### Goal

Catch regressions in argument validation, ownership checks, and error-shape consistency across the ~35 new tools added in Phases A/B/C.

### Approach

Per-area test files following the `test_ssrf.py` pattern (in-process MCP server build + direct tool invocation):

- `test_tools_chemistry.py` — `name_to_structure`, `patent_coverage`, `propose_retrosynthesis`, `compound_similarity_search`, `reaction_similarity_search`, `substructure_search`
- `test_tools_papers.py` — `paper_qa`, `register_paper`, `lookup_knowledge`
- `test_tools_investigations.py` — all 9 Phase B tools (3 investigations + 3 world-model + 3 hypotheses)
- `test_tools_sandbox.py` — `run_code`, `list_code_executions`
- `test_tools_optimization.py` — `propose_next_conditions`, `declare_campaign_parameter_space` (after §A)

Each test:
1. Invokes happy-path with valid args, asserts return-shape keys.
2. One bad arg per validation rule, asserts error message contains the rule.
3. Ownership boundary: invokes with a stranger's id, asserts fail-closed (`{"error": ...}` or `{"ok": False}`).

### Key decisions

- **In-process MCP server build** — no subprocess; uses `build_chemclaw_mcp_server(user_id, session_id, session_factory)` directly. Lets us inject a real session_factory backed by the CI Postgres.
- **No fakery for the underlying queries** — tests run against real DB so the SQL itself is exercised.
- **One test per validation rule, not per tool** — keeps coverage explicit and per-failure debugging fast.

### Trade-offs

- **~35 tools × ~3-5 test cases each = ~150 tests.** Spread across 5-6 files, parallelisable by pytest-xdist later if collection time matters.

### Effort

M. ~800 LOC across 5–6 test files. Plan: one PR per area to keep review tractable.

---

## F. Chunk-size / overlap config via env vars

### Goal

Surface `PAPER_CHUNK_SIZE` / `PAPER_CHUNK_OVERLAP` env vars so a deployment can re-tune without code change.

### Approach

`_ingest_paper_chunks` in `api/agent/tools.py`:
```python
chunk_size = int(os.environ.get("PAPER_CHUNK_SIZE", "1500"))
overlap    = int(os.environ.get("PAPER_CHUNK_OVERLAP", "200"))
if not (200 <= chunk_size <= 5000) or not (0 <= overlap < chunk_size):
    logger.warning("invalid PAPER_CHUNK_* env; falling back to defaults")
    chunk_size, overlap = 1500, 200
parts = chunk_paper_text(content_text, chunk_size=chunk_size, overlap=overlap)
```

### Key decisions

- **Validation at read time, not at chunk_paper_text** — `chunk_paper_text` already has internal clamping; this layer just complains and falls back so misconfigured envs don't silently produce 50-char chunks.

### Trade-offs

None.

### Effort

XS. ~10 LOC + a unit test for the validation fallback.

---

## G. OpenAI fallback for RCS scoring

### Goal

Add `RCS_PROVIDER=openai` path so deployments that can't use Anthropic for any reason still get PaperQA2-style RCS reranking.

### Approach

`api/db/queries/paper_rcs.py:score_chunks_with_llm`:
- Read `RCS_PROVIDER` env (default: `anthropic`).
- Provider-specific lazy client: `_get_anthropic_client()` already exists; add `_get_openai_client()` mirroring the `api/embeddings.py` pattern.
- Provider-specific model env: `ANTHROPIC_RCS_MODEL` / `OPENAI_RCS_MODEL` with sensible defaults (`claude-haiku-4-5-20251001` for Anthropic; for OpenAI, the then-current small reasoning model — pick at implementation time rather than pinning here).
- Same `RCS_PROMPT` + same `_extract_json_object` parser — both providers return JSON the same way.

### Key decisions

- **Single prompt, two providers.** The prompt is provider-neutral. JSON output extraction is unchanged. Cuts complexity vs. provider-specific prompt templates.
- **Provider-specific fail-closed.** If `RCS_PROVIDER=openai` but `OPENAI_API_KEY` missing, surface the rcs_error per chunk; don't silently retry on Anthropic.

### Trade-offs

- **+OpenAI SDK is already in deps** (used for embeddings), so no new dep weight.

### Effort

S. ~80 LOC + 2 tests (one per provider, with stubbed client responses).

---

## H. External retrosynthesis service (AiZynthFinder)

### Goal

Add a second `propose_retrosynthesis_deep` tool that does full multi-step retrosynthetic search, vs. the current 11-template single-step library.

### Approach

**Library:** [AiZynthFinder](https://github.com/MolecularAI/aizynthfinder) (AstraZeneca, MIT). Pure Python, runs locally. Two model bundles ship: the public demo policy + filter (~500 MB, suitable for first deployment / dev) and the full USPTO-trained bundle (~4 GB, production). Start with the demo bundle, upgrade when route quality is measured as the bottleneck.

**Integration shape:**
- New MCP server `mcp_retrosynth_deep` wrapping `aizynthfinder.aizynthfinder.AiZynthFinder`.
- Model files downloaded on first run via the official `download_public_data` helper; cached under `/var/cache/aizynthfinder/` (volume-mounted in production).
- Tool returns the top-K full route trees, not single-step disconnections.

```python
@mcp.tool()
async def propose_retrosynthesis_deep(target_smiles: str, max_routes: int = 5) -> dict:
    finder = _get_finder()
    finder.target_smiles = target_smiles
    await asyncio.to_thread(finder.tree_search)
    finder.build_routes()
    routes = finder.routes
    return {"target": target_smiles, "routes": _serialise(routes[:max_routes])}
```

### Key decisions

- **Keep single-step `propose_retrosynthesis` as the fast path.** Deep search takes 30s–5min per target. The agent uses the fast tool for first-pass exploration; the deep tool when the fast one comes back empty or when committing to a route.
- **Cache target → routes in `external_facts`** keyed by `aizynth:<canonical_smiles>` with a 30-day TTL. Same pattern as `name_to_structure`'s CACTUS cache.
- **Model-file download is a one-shot ops step**, not a per-container concern. Document in the Dockerfile.

### Trade-offs

- **~4 GB image growth.** Mitigation: separate Dockerfile stage that pulls models lazily; only deploy on workers that need deep retrosynthesis.
- **Wall-clock cost.** AiZynthFinder's tree search isn't async; we offload to `asyncio.to_thread`. Wall-cap at 5 min default.

### Effort

L. New MCP package + Docker delta + ops doc. Plan: one PR for the package, one for the Dockerfile + ops integration.

---

## I. `_probe_unshare` lazy

### Goal

Stop blocking module import for up to 2s while probing `unshare -n -r true`.

### Approach

```python
@functools.cache
def _unshare_available() -> bool:
    """Probed once on first sandbox invocation; cached for the process."""
    unshare = shutil.which("unshare")
    if unshare is None:
        return False
    ...
```

Call from `_build_command` instead of at module load. First sandbox call eats the 2s; subsequent calls are cached.

### Key decisions

- **`functools.cache`** rather than a module-level `bool` — keeps the probe code path obvious and testable.

### Trade-offs

- **Cache is process-lifetime.** If the host's `unshare` permissions actually change at runtime (extremely unusual — kernel-level capability changes), the cached probe stays stale until restart. Acceptable; capability swaps mid-process don't happen in any production deploy.

### Effort

XS. 1 decorator + move call site.

---

## J. mypy CI gate re-enablement

### Goal

Drop `continue-on-error: true` from the mypy step so type bugs block CI again.

### Approach

The blocker today is *visibility* — CI mypy fails with errors that local mypy 1.19.1 (pinned) does not, and we couldn't read CI's mypy output via WebFetch. Three options to break the impasse:

1. **Dump-and-annotate (recommended).** Mirror the pytest pattern from PR #117: `mypy api/ 2>&1 | tee /tmp/mypy.log` + on-failure `tail` + grep + `::error::` annotations + artifact upload. Once CI mypy errors are visible in the run summary, fix them, then drop `continue-on-error`.
2. **Reproduce locally with CI's deps.** Run the CI install steps in a clean Docker container locally; reproduce the error; pin the dep that drifted.
3. **Live without mypy.** Treat the gate as permanently soft. Don't recommend.

### Key decisions

- Go with option 1. The pytest infrastructure from PR #117 already provides the pattern; mirror it.

### Trade-offs

- One extra CI step + artifact storage. Negligible.

### Effort

S. ~30 LOC of CI yaml + iteration time to find and fix the actual mypy error(s).

---

## K. Process-leak fix on non-Timeout sandbox exceptions

### Goal

Ensure the sandbox child is always reaped, including on exceptions other than `asyncio.TimeoutError`.

### Approach

Wrap the entire `proc.communicate()` block in an outer `try/finally`:

```python
proc = await asyncio.create_subprocess_exec(...)
try:
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=wall_seconds)
        ...  # existing happy path
    except asyncio.TimeoutError:
        ...  # existing timeout handling
finally:
    if proc.returncode is None:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await proc.wait()
        except Exception:
            logger.exception("sandbox_reaper_failed pid=%s", proc.pid)
```

### Key decisions

- **`returncode is None` guard** — only reap if the process is still alive. Avoids double-wait on the happy path.
- **Log the reaper failure** instead of raising — a raise in `finally` would mask the original exception.

### Trade-offs

None.

### Effort

XS. 5 LOC.

---

## L. `code_executions` query-layer ownership validation

### Goal

Belt-and-suspenders: validate investigation ownership at the SQL level inside `insert_execution` so future callers can't accidentally bypass the tool-layer check.

### Approach

```python
async def insert_execution(
    db, *, code, ..., created_by, investigation_id=None, session_id=None,
) -> str:
    ...
    async with db.begin():
        if investigation_id is not None:
            result = await db.execute(
                text("""
                    INSERT INTO code_executions (...)
                    SELECT CAST(:iid AS uuid), :sid, :code, ..., :uid
                    WHERE EXISTS (
                        SELECT 1 FROM investigations
                         WHERE id = CAST(:iid AS uuid)
                           AND created_by = :uid
                    )
                    RETURNING id::text
                """),
                {...},
            )
            row = result.first()
            if row is None:
                raise ValueError("investigation not owned by created_by")
            return row[0]
        # session-only path unchanged
        ...
```

### Key decisions

- **EXISTS-gated INSERT** keeps it atomic — no separate SELECT-then-INSERT race.
- **`raise ValueError`** so the tool layer's existing `try/except ValueError` catches it.

### Trade-offs

- One extra subquery on every execution insert. Sub-millisecond on `investigations(id, created_by)` index hit.

### Effort

S. ~30 LOC + 1 test.

---

## M. Figure capture from sandbox

### Goal

Let agent-written code emit matplotlib figures and have them surface back through `run_code` as base64-encoded artefacts.

### Approach

1. **Migration 0042:** add `code_executions.artifacts JSONB NOT NULL DEFAULT '[]'::jsonb` storing `[{filename, mime, size_bytes, b64}]` per row.
2. **Sandbox prelude.** The sandbox prepends a setup snippet before user code:
   ```python
   import matplotlib
   matplotlib.use("Agg")
   import matplotlib.pyplot as _plt
   _plt.rcParams["savefig.directory"] = "."  # cwd is the ephemeral tempdir
   ```
3. **Post-run scan.** After the child exits, the sandbox walks the tempdir for `*.png` / `*.svg`, base64-encodes (up to 2 MB total per execution), and returns them as `artifacts: [...]` in the `SandboxResult`.
4. **Tool wrapper.** `run_code` persists `artifacts` to the new column and returns it in the response.

### Key decisions

- **Agg backend** — no display required, headless rendering.
- **PNG + SVG only** — JPG/etc. unnecessary for analytical figures. Whitelist the extensions to keep the surface clean.
- **2 MB total cap** — figures are bytes, not analytical data. Anything bigger means the agent is trying to exfiltrate.
- **Store base64 in JSONB, not BYTEA.** Trades a bit of size for keeping artefacts inline with the execution row — same JSONB pattern as `payload` elsewhere.

### Trade-offs

- **JSONB blow-up on large figures.** Cap mitigates. Could move to a separate `code_execution_artifacts` table later if needed.

### Effort

M. Migration + sandbox library change + tool wrapper update + tests. ~200 LOC.

---

## Cross-cutting notes

- **All items follow CLAUDE.md owner-scoping discipline** (denormalize `created_by`, predicate every UPDATE/DELETE on `user_id = :uid`).
- **Every item has BACKLOG-entry-friendly atomic scope** — none are larger than a single PR; the largest (§A, §B, §H) are sized for ≤ 1500 LOC each.
- **§A specifically uses BOFIRE** per the instruction to standardise on off-the-shelf BO tooling rather than self-build.

## Out of scope for this concept

- Workflow / multi-step agent orchestration beyond what Phase C shipped — that's a Phase D conversation.
- LLM evals against a golden chemistry Q&A set (already filed under Tier F in BACKLOG).
- Multi-tenant RLS (Tier F).
- Anything that requires changes to the `typescript_old` archive.
