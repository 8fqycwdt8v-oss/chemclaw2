# Tier 3 implementation plan

Four items: **§A** real BO via BOFIRE · **§B** container-isolated sandbox · **§H** external retrosynthesis · **§M** figure capture from sandbox. Each is sized for one PR. Per the standing instruction, the BO design is reworked to be *as light as possible while still performant and powerful* — see §A.

## Sequencing

| Order | Item | Why this order |
|---|---|---|
| 1 | **§A** Lightweight BO | Highest user value (replaces V1 heuristic); other items don't depend on it but it unblocks campaign-optimisation workflows that are blocked today |
| 2 | **§M** Figure capture | Small, builds on existing `mcp_codesandbox` we'd be touching anyway in §B |
| 3 | **§B** Container sandbox | Deployment-touching; do after §M so we don't have to revisit `mcp_codesandbox` twice |
| 4 | **§H** External retrosynthesis | Largest ops footprint (~500 MB model bundle); does not block anything else |

Items are independent — could ship in any order. The sequence above optimises for "user-visible value first" then "ops complexity last."

---

## §A. Lightweight BO via BOFIRE

### Goal

Replace the V1 `propose_next_conditions` heuristic with a Bayesian-optimisation loop that uses **BOFIRE** as the off-the-shelf framework, but keeps the base chemclaw2 install slim. Users who don't need BO pay no torch tax; users who do `pip install chemclaw2-backend[opt]` get the full GP+EI path.

### Design — two-stage BO

The key insight: **BOFIRE is two libraries.** The schema layer (`bofire.data_models.*` — `Domain`, `Input`, `Output`, `Constraint`) is pure pydantic, ~5 MB of deps. The strategy layer (`bofire.strategies.*` — `SoboStrategy`, `MoboStrategy`, surrogates) pulls BoTorch + GPyTorch + torch, ~2 GB.

Run them at different tiers:

| Stage | Always-on? | Deps | What it does |
|---|---|---|---|
| **0** Heuristic (today's V1) | yes | none | Best-yield exploit + temperature tweak + solvent swap |
| **1** Schema + LHS / random | yes | `bofire>=0.0.13` (~5 MB, pydantic only) | Validate parameter spec; emit Latin-Hypercube or random proposals from BOFIRE Domain |
| **2** GP + qLogEI | opt-in via `[opt]` extras | `bofire[optimization]` (+ torch ~2 GB) | Real surrogate-driven BO |

Per-call selection logic in `propose_next_conditions`:

```
1. If no parameter_spec exists → stage 0 (V1 heuristic). Backwards compat.
2. If parameter_spec exists but completed_steps < 10 → stage 1
   (BOFIRE LHS — better diversity than V1's hand-rolled solvent swap).
3. If completed_steps >= 10 AND bofire[optimization] importable → stage 2
   (GP + qLogEI for the win).
4. If completed_steps >= 10 AND bofire[optimization] NOT importable → stage 1
   + a warning log: "install chemclaw2-backend[opt] for surrogate-driven BO".
```

This makes the upgrade path painless: customers start with stage 0 (free), declare a parameter spec to get stage 1 (free), then `pip install` for stage 2. No code changes needed at each tier.

### Schema delta

New optional JSON sub-document inside `synthesis_campaigns.plan`:

```json
{
  "parameter_spec": {
    "inputs": [
      {"key": "temperature", "type": "continuous", "min": 20, "max": 120, "unit": "C"},
      {"key": "solvent", "type": "categorical", "categories": ["THF", "DMF", "EtOH"]},
      {"key": "catalyst_loading_mol_pct", "type": "continuous", "min": 0.5, "max": 10.0}
    ],
    "outputs": [
      {"key": "yield_pct", "type": "continuous", "direction": "maximize"}
    ]
  }
}
```

No migration needed — `synthesis_campaigns.plan` is already JSONB. Add Pydantic models in a new `api/agent/parameter_spec.py` so the spec is validated when the agent declares it, and translate that to BOFIRE's `Domain` at use-time.

### New tools

- `declare_campaign_parameter_space(campaign_id, parameter_spec)` — validates against the Pydantic model, persists into `plan.parameter_spec`. Owner-scoped.
- `propose_next_conditions` (existing) — rewritten to dispatch stage 0 / 1 / 2 per the matrix above.

### Outcomes feed

Stage 1 and 2 read from **`reaction_outcomes JOIN campaign_steps`** (PR #115's structured table) — not `campaign_steps.result` JSONB. Each row becomes a BOFIRE `Experiment` with `pd.DataFrame` row keyed by the parameter spec's input keys + output keys.

Partial-observation handling: stage 2 drops rows with NULLs in declared outputs. Stage 1 (LHS) doesn't care; it doesn't use outcomes for proposal generation.

### Key decisions — kept tight

| Decision | V1 choice | Why |
|---|---|---|
| Strategy | `SoboStrategy` only | Single-objective covers ~80% of real campaigns (max yield). MoboStrategy (multi-objective) deferred until measured demand. |
| Surrogate | `MixedSingleTaskGPSurrogate` (BOFIRE default) | Robust for mixed cat+cont at small N. No need to tune. |
| Acquisition | `qLogExpectedImprovement` (BOFIRE default) | Numerically stable. Single function call. |
| Initial sampling | LHS via BOFIRE `RandomStrategy` | Better than uniform for diversity at small N. |
| Stage 1 → 2 trigger | `len(completed) >= 10` | Hard cutoff; GP fit before 10 datapoints is noise. Made overrideable via `BO_MIN_DATAPOINTS` env. |
| Categorical cap | 8 levels max | One-hot encoded; >8 levels explodes parameter space. Reject at parameter-spec validation time. |
| Constraints | Box constraints only in V1 | BOFIRE supports `LinearInequalityConstraint` etc., but the spec / agent UX would need to grow. Defer. |

### What we do **not** do

- ❌ Multi-objective (deferred until customer asks)
- ❌ Mixture variables (solvent mixtures with sum-to-1 constraint — deferred)
- ❌ Transfer learning / multi-task GPs (deferred — premature for V1)
- ❌ Per-call BOFIRE strategy override (model + acquisition are fixed at the env-var level only)
- ❌ Custom surrogate fitting parameters — use BOFIRE defaults

This cuts the implementation to roughly: parameter_spec models + Pydantic validation + 1 new tool + the staged dispatcher in `propose_next_conditions` + a 3-mode unit test. Estimated ~400 LOC excluding the BOFIRE dep declaration, well under the "L" budget.

### Trade-offs

- **+5 MB always-on for the schema layer.** Negligible. Equivalent to bumping pydantic patch version.
- **+~2 GB only when `[opt]` extras installed.** Acceptable — explicit user opt-in, and only on the worker(s) that run optimisation. The agent runtime can stay slim.
- **Stage 1 (LHS without GP) isn't really BO.** Honest about it in the tool's response: `strategy: "lhs-no-surrogate"`. The agent can read this and tell the user "more datapoints will unlock GP-driven proposals."
- **`completed >= 10` threshold is a guess.** Documented as overrideable via env. Real value depends on parameter-space dimensionality; a 2-D problem fits a useful GP at N=5, a 10-D problem needs N≥30. Acceptable for V1; can grow into a dimension-aware threshold later.

### Effort

**M** (not L as originally scoped). Aggressive scope-trimming + opt extras for the heavy dep + reusing existing `reaction_outcomes` reduces this from "~600 LOC + image bloat" to "~400 LOC + opt-in image bloat."

---

## §B. Container-isolated sandbox via bubblewrap

### Goal

Move `mcp_codesandbox` from `subprocess + RLIMIT` to bubblewrap-isolated execution where the host environment allows it, dropping back gracefully when it doesn't (Docker-in-Docker, no CAP_SYS_ADMIN).

### Approach

Same as the original concept §B. Probe-and-cache pattern (mirrors `_unshare_available`) detects bubblewrap availability:

```python
@functools.cache
def _bwrap_available() -> bool:
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        return False
    try:
        r = subprocess.run([bwrap, "--version"], capture_output=True,
                           timeout=2.0, check=False)
        if r.returncode != 0:
            return False
        # Smoke-test that --unshare-all actually works on this host.
        smoke = subprocess.run(
            [bwrap, "--unshare-all", "--die-with-parent",
             "--ro-bind", "/usr", "/usr",
             "/usr/bin/true"],
            capture_output=True, timeout=2.0, check=False,
        )
        return smoke.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
```

If available, `_build_command` returns the bwrap argv; otherwise falls back to the existing `unshare`/plain subprocess path. Trust boundary unchanged on hosts without bwrap.

### Key decisions

- **Bwrap profile is minimal.** Only the flags needed for the sandbox guarantee. No firejail-style declarative profiles to maintain.
  - `--ro-bind /usr /usr` + `--ro-bind /lib /lib` + `--ro-bind /lib64 /lib64` (read-only system libs)
  - `--ro-bind /etc/python3.11 /etc/python3.11` (Python's config)
  - `--tmpfs /tmp` + `--tmpfs /home` (ephemeral writable)
  - `--proc /proc` + `--dev /dev`
  - `--unshare-all` (pid, net, ipc, uts, cgroup, mount, user)
  - `--die-with-parent`
  - `--cap-drop ALL`
- **Network drop is real now.** Existing best-effort `unshare -n` becomes default-deny. Agents that need network must request it via a new `network=True` arg on `run_code`. V1 doesn't ship this arg — explicit follow-up if needed.
- **Docker-in-Docker fallback documented.** The bwrap probe runs the smoke test, so containerised hosts where `--unshare-all` fails simply fall back to `unshare/subprocess`. No deploy break.
- **`bubblewrap` apt package added to Dockerfile** as part of this PR.

### What we do **not** do

- ❌ Per-call profile customisation (read/write paths beyond defaults)
- ❌ Bind-mount of host data into sandbox (no FS sharing — fresh tempdir is enough)
- ❌ Seccomp policy beyond what bwrap defaults give
- ❌ Persistent bwrap "container" reuse (each call is a fresh bwrap invocation)

### Trade-offs

- **+~50 KB binary in the image.** Negligible.
- **~20ms launch latency vs. plain subprocess.** Acceptable.
- **Real network drop breaks any sandbox code that previously expected outbound HTTP.** Mitigation: explicit comm in PR description + a follow-up `network=True` opt-in if anyone hits it.

### Effort

**M.** ~250 LOC sandbox change + Dockerfile delta + bwrap probe test. Sized down from the original "L" by keeping the profile minimal and the fallback path identical to today's.

---

## §H. External retrosynthesis via AiZynthFinder

### Goal

Add a second `propose_retrosynthesis_deep` tool that does full multi-step retrosynthetic search, complementing the 11-template single-step library.

### Approach

**Lightweight V1: demo bundle only.** AiZynthFinder ships two model bundles:
- **Demo** (~500 MB total) — `download_public_data` helper pulls a small USPTO policy + filter
- **Full USPTO** (~4 GB) — production-grade, deeper search

V1 ships only the demo bundle path. Document in the PR that customers can upgrade to full USPTO by overriding the `AIZYNTH_CONFIG_PATH` env var to point at a full-bundle config they've prepared themselves. Avoids a 4 GB image baseline.

### Integration

New MCP server `mcp_retrosynth_deep`:

```
packages/mcp-servers/mcp_retrosynth_deep/
├── pyproject.toml      # deps: aizynthfinder>=4.0
└── mcp_retrosynth_deep/
    ├── __init__.py
    └── server.py       # @mcp.tool() propose_retrosynthesis_deep(target_smiles, max_routes)
```

Model files cached under `/var/cache/aizynthfinder/`. First call to the tool triggers `download_public_data` if the cache is empty (one-time ~30s on cold container).

New agent tool `propose_retrosynthesis_deep(target_smiles, max_routes=5)`:
- Wraps the MCP call via `asyncio.to_thread` (AiZynthFinder's tree_search is sync)
- Returns full route trees (parent → children) as nested JSON
- Wall-cap at 5 min default; tool arg `max_seconds` lets the agent shorten
- Caches `target_smiles → routes` in `external_facts` with 30-day TTL (same pattern as `name_to_structure`)

The existing fast-path `propose_retrosynthesis` stays — agent uses it for first-pass disconnection enumeration, falls to `_deep` when committing to a route or when fast-path returns empty.

### Schema delta

None — `external_facts` already exists; cache key is `aizynth:<canonical_smiles>`.

### Key decisions

- **Demo bundle only in V1.** Full USPTO is an opt-in via env override; no image bloat.
- **No tree visualisation.** Routes returned as nested JSON. Visualisation is a UI concern, not the agent's.
- **Wall-cap mandatory.** 5 min default; AiZynthFinder can run for minutes on hard targets.
- **Single execution model.** Synchronous in-process via `asyncio.to_thread`. No worker queue, no batching — V1 doesn't need it.

### What we do **not** do

- ❌ Multi-tenant model selection (one config per deployment)
- ❌ Cache eviction policy beyond TTL
- ❌ Reaction-feasibility scoring on top of AiZynthFinder's output
- ❌ Integration with `confirm_synthesis_plan` auto-population (agent does that manually if desired)

### Trade-offs

- **~500 MB image growth** unless deployed on a separate worker. Recommend a dedicated worker container for retrosynthesis if traffic justifies.
- **First-call latency** for model download. Mitigation: pre-warm in the Dockerfile's RUN step (one extra layer, +500 MB at image build time but zero cold-start cost).

### Effort

**M.** New MCP package + Dockerfile delta + opt env documentation. ~300 LOC.

---

## §M. Figure capture from sandbox

### Goal

Let agent-written code emit matplotlib figures via the sandbox and surface them back as base64-encoded artefacts in the `code_executions` row.

### Approach

1. **Migration 0042:** add `code_executions.artifacts JSONB NOT NULL DEFAULT '[]'::jsonb` storing `[{filename, mime, size_bytes, b64}]`.
2. **Sandbox prelude.** `mcp_codesandbox/sandbox.py` prepends a small setup snippet to every user code submission:
   ```python
   import matplotlib
   matplotlib.use("Agg")
   ```
   Just enough to make plotting work headless. Users still write their normal `plt.plot(...)` / `plt.savefig(...)` calls.
3. **Post-run scan.** After the child exits successfully, the sandbox walks `tmpdir` for `*.png` files, base64-encodes (up to ~1.5 MB total per execution to leave headroom under the 2 MB JSONB row sweet spot), and returns them via a new `SandboxResult.artifacts` field.
4. **Tool wrapper.** `run_code` persists `artifacts` to the new column and returns them in the response. `list_code_executions` returns artifact metadata (filename + mime + size) without the b64 payload, to keep list responses small.

### Key decisions

- **PNG only.** No SVG/PDF/HTML to keep parser/cap logic uniform.
- **`matplotlib.use("Agg")` is the only prelude.** Don't pre-import pandas/numpy — let users do that.
- **1.5 MB total cap per execution.** Larger artefacts get the first-N files until cap; rest dropped with a `[sandbox] artifact truncated` marker added to stderr.
- **Metadata-only `list_code_executions`.** Full b64 only via `get_execution(execution_id)`. List responses stay paginatable.

### What we do **not** do

- ❌ SVG / PDF / HTML artefact types (PNG only)
- ❌ Plotly / Bokeh interactive widgets (raster only)
- ❌ Object storage for artefacts (inline JSONB is enough at the V1 cap)
- ❌ Artefact thumbnails for the list view

### Trade-offs

- **JSONB row size up to ~2 MB.** Postgres TOAST handles this transparently. Fine.
- **No image dedup.** Two identical figures across two executions get stored twice. Acceptable; optimise later if storage becomes an issue.

### Effort

**M.** Migration + sandbox library change (~80 LOC) + tool wrapper update (~40 LOC) + tests. ~200 LOC.

---

## Cross-cutting

### Dependency strategy

Three new `pyproject.toml` extras introduced across Tier 3:

| Extras | Adds | When to install |
|---|---|---|
| `[opt]` | `bofire[optimization]` (→ torch, BoTorch, GPyTorch) | Real surrogate-driven BO on the worker(s) running campaigns |
| `[retrosynth]` | `aizynthfinder>=4.0` | Workers running `propose_retrosynthesis_deep` |

Both extras are opt-in — the base `pip install -e .` stays slim. Documentation: a single README section listing which workloads need which extras.

The lightweight BOFIRE schema layer (`bofire>=0.0.13` only) lands in the default deps so parameter specs validate everywhere.

### Mypy gate

Once Tier 1's mypy log surfacing has been exercised on at least one real CI failure, the `continue-on-error: true` on the mypy step gets dropped. That cleanup is implicit follow-up; not part of Tier 3.

### Test coverage

Each Tier 3 PR ships:
- Unit tests for the pure-Python pieces (parameter spec validation, sandbox prelude generation, AiZynthFinder result-parsing helpers, bwrap probe)
- DB integration tests for any schema delta (§M's artifacts column)
- Smoke tests for tool wrappers as far as the existing test infrastructure allows. (§E full tool-layer smoke test harness still BACKLOG.)

### What stays in BACKLOG after Tier 3

- §E tool-layer smoke-test harness (needs SDK design work)
- §A multi-objective BO + mixture variables
- §B per-call network opt-in (`network=True` on `run_code`)
- §H full USPTO model bundle as default
- §M SVG / Plotly / PDF support; object-storage backing for large artefacts
