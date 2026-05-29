# Competitive & deep-research analysis — chemclaw2

_Date: 2026-05-29. Method: fan-out web research across six angles + a full
codebase map, adversarially synthesized. Every recommendation is tied to a
source and scoped against chemclaw2's actual stack (Claude Agent SDK,
FastAPI, Postgres + pgvector, RDKit/DRFP via MCP, OpenAI embeddings)._

chemclaw2 is a knowledge-intelligence agent for pharma R&D with three
surfaces: a conversational agent, a living wiki, and chemistry-native
search. It is already mature — 43 agent tools, 6 MCP servers, hybrid
FTS+semantic retrieval, RCS-style reranking (`paper_rcs.py`), a bi-temporal
wiki with audit trail + contradictions, BOFIRE Bayesian optimization,
AiZynthFinder retrosynthesis, a tiered sandbox, and Entra auth. The goal of
this pass was to find genuinely _additive_ improvements, not to reinvent
what exists.

## TL;DR — what shipped vs. what's queued

- **Shipped in this pass:** the fingerprint similarity-metric fix (see §1).
  A real correctness bug: the ANN index pruned candidates by Hamming
  distance while results were ranked by Tanimoto.
- **Queued in `BACKLOG.md`** (ranked): RDKit cartridge substructure search,
  standardize-on-register + InChIKey dedup, two-stage rerank, Falconer-style
  staleness loop, LitQA2-style eval in CI, Langfuse/OTel observability,
  Citations-style grounding, R-group/SAR tables, ORD-style reaction model,
  Onyx-style incremental connectors, abstention-as-first-class.

---

## 1. The metric-mismatch fix (shipped)

**Finding (verified in code).** `migrations/0002_chem.sql` built the HNSW
indexes on `compounds.morgan_fp` and `reactions.drfp` with
`bit_hamming_ops`, and the similarity queries ordered by `<~>` (Hamming
distance) before re-ranking the top-100 candidates by **Tanimoto** in app
code (`api/db/queries/fp_utils.py:rerank_by_tanimoto`). For binary chemical
fingerprints the field-standard similarity is **Tanimoto = Jaccard**, not
Hamming; Hamming counts differing bits and is biased by total bit-density,
so two large dissimilar molecules can look "close." Because the ANN graph
pruned the candidate pool by a metric that does not match the final
ranking, true top-Tanimoto neighbours could be dropped before the reranker
ever saw them — a silent recall bug at scale.

**Fix.** Replace the indexes with `bit_jaccard_ops` (operator `<%>`,
Jaccard distance = 1 − Tanimoto) and order the candidate queries by `<%>`,
so ANN pruning and final ranking use the same metric. No new dependencies,
no Postgres-extension requirement (pgvector ≥ 0.7 ships `bit_jaccard_ops`).
Migrations `0045`–`0048` (drop + recreate, `CONCURRENTLY`, single-statement
per the migration policy); query changes in `compounds.py` / `reactions.py`;
verification in `api/tests/test_fingerprint_similarity_metric.py`.

Sources: [pgvector README](https://github.com/pgvector/pgvector),
[pgvector 0.7.0 (Supabase)](https://supabase.com/blog/pgvector-0-7-0),
[RDKit Cartridge docs](https://www.rdkit.org/docs/Cartridge.html),
[DRFP (Digital Discovery 2022)](https://pubs.rsc.org/en/content/articlehtml/2022/dd/d1dd00006c).

---

## 2. Cheminformatics search & registry platforms

Closest comparables: **RDKit Postgres cartridge / ChEMBL / lwreg** (the
open reference architecture), **CDD Vault** (closest commercial registry
SaaS), **ChemAxon JChem** (gold standard for standardization/tautomer
semantics), **Schrödinger LiveDesign** (SAR/R-group surface), **Open
Reaction Database** (reaction schema), **PubChem** (web-scale search API).

Top patterns to adopt:

1. **Two-stage substructure search: pattern-fingerprint screen → exact
   RDKit match.** pgvector cannot do subgraph matching at all; the cartridge's
   GiST pattern-fp pre-filter cuts exact matches ~99.97%. Either adopt the
   `rdkit` cartridge (`mol` column + GiST `@>`) — _if the deployment Postgres
   can load the extension_ — or add an RDKit pattern-fp screening column as a
   pgvector-only middle ground.
2. **Standardize once, identically, for query and stored structure**
   (`rdMolStandardize`: Cleanup → LargestFragmentChooser → Uncharger →
   TautomerEnumerator.Canonicalize), then **dedup on the Standard InChIKey of
   the parent**, with Morgan fp as the similarity layer (two distinct
   mechanisms — don't conflate). Today only `canon_smiles` is stored.
3. **Molecule ↔ Batch/Lot two-tier registry** with a register-time "new
   molecule vs. new batch of existing" path + SDfile bulk import (CDD Vault).
4. **Tautomer handling varies by search type:** generic/any-tautomer for
   substructure (never canonicalize a fragment query), canonical-tautomer key
   for exact/dedup (ChemAxon).
5. **R-group decomposition / SAR tables** via `rdRGroupDecomposition`
   (Schrödinger LiveDesign) as the "scaffolds in the library" output.
6. **Reactions:** ORD-style decomposed records + SMARTS substructure on
   reactant/product sets separately, alongside DRFP whole-reaction similarity.
   Keep DRFP (no training, indexes natively); rxnfp only if learned semantic
   neighbors become a hard requirement.
7. **Async job + poll** for whole-library scans (PubChem PUG-REST), mapping
   onto the existing asyncio worker + advisory locks.

Sources: [RDKit Cartridge](https://www.rdkit.org/docs/Cartridge.html) ·
[RDKit MolStandardize](https://www.rdkit.org/docs/source/rdkit.Chem.MolStandardize.rdMolStandardize.html) ·
[ChEMBL curation pipeline (Bento et al.)](https://www.ncbi.nlm.nih.gov/pmc/articles/PMC7458899/) ·
[lwreg (JCIM 2024)](https://pubs.acs.org/doi/10.1021/acs.jcim.4c01133) ·
[CDD Vault structure search](https://support.collaborativedrug.com/hc/en-us/articles/360044466812-Structure-Searching-in-CDD-Vault) ·
[ChemAxon JChem search types](https://docs.chemaxon.com/display/docs/jchem-base_search-types.md) ·
[Schrödinger R-group decomposition](https://learn.schrodinger.com/public/python_api/2024-3/api/schrodinger.livedesign.rgroup_decomposition.html) ·
[Open Reaction Database (JACS)](https://pubs.acs.org/doi/10.1021/jacs.1c09820) ·
[PubChem in 2021 (NAR)](https://academic.oup.com/nar/article/49/D1/D1388/5957164).

---

## 3. AI agents / copilots for scientific & pharma R&D

Closest comparables: **FutureHouse PaperQA2** (open-source agentic literature
RAG — study first), **ether0/Aviary** (chemistry reasoning + agent-env
harness), **Causaly** & **BenchSci** (KG-grounded answers + provenance for
regulated use), **Elicit** (structured extraction at scale), **Scite**
(directional support/contradict citations), **Insilico** & **Iktos** &
**Chai** (specialized tool-chaining: generative chemistry, retrosynthesis,
structure prediction).

Top patterns to adopt:

1. **PaperQA2's small agentic loop** (`search → gather_evidence →
   generate_answer`, called iteratively — >4 tool calls/question) as the
   retrieval blueprint; the non-determinism is where accuracy comes from.
2. **RCS (Rerank + Contextual Summarization)** as a middle stage — a cheap
   per-chunk LLM call returns a query-conditioned summary + relevance score;
   only top summaries enter the answer prompt. chemclaw2 already has this in
   `paper_rcs.py`; keep investing here, it's the single highest-leverage
   grounding technique.
3. **Abstention as a first-class, evaluated outcome** ("insufficient
   evidence" beats a wrong answer — PaperQA2's precision ≫ accuracy because it
   can abstain; Causaly's SIRS treats no-answer as correct).
4. **Passage-level (ideally figure/assay-level, à la BenchSci) citations**
   keyed to stable chunk IDs, with a persisted claim→chunk_id→source map.
5. **Directional support/contradict/mention evidence + a ContraCrow-style
   sweep** that checks each wiki claim against new/conflicting papers
   (ContraCrow found ~2.3 contradictions/biology paper). Extends the existing
   `wiki_contradictions` table.
6. **A LitQA2-style held-out benchmark from day one**, scored on precision +
   accuracy + citation-correctness, run in CI; track answer correctness and
   justification consistency separately (Elicit: answers stable while
   citations drift).
7. **Wrap specialized chemistry models as MCP tools, don't rebuild them**
   (Iktos Spaya-style retrosynthesis, ether0 Apache-2.0 molecular reasoning,
   Chai-1 structure) — matches chemclaw2's "off-the-shelf over self-built."

Sources: [Future-House/paper-qa](https://github.com/Future-House/paper-qa) ·
[PaperQA2 (arXiv 2409.13740)](https://arxiv.org/abs/2409.13740) ·
[ether0](https://www.futurehouse.org/research-announcements/ether0-a-scientific-reasoning-model-for-chemistry) ·
[Causaly agentic platform](https://www.causaly.com/) ·
[BenchSci ASCEND](https://blog.benchsci.com/introducing-ascend-by-benchsci) ·
[Elicit systematic review](https://elicit.com/blog/systematic-review/) ·
[scite (MIT Press QSS)](https://direct.mit.edu/qss/article/2/3/882/102990/) ·
[Iktos Spaya](https://iktos.ai/solution/spaya) ·
[chai-lab](https://github.com/chaidiscovery/chai-lab).

---

## 4. Enterprise RAG & living/AI-maintained wikis

Closest comparables: **Onyx (Danswer)** — open-source, the best
architectural reference; **Glean** — canonical ranking + permission
governance; **Dust**; **Notion AI** (autonomous wiki agents); **Falconer**
(purpose-built living documentation — closest to chemclaw2's wiki surface);
**DeepWiki** (auto-generated wiki from a corpus); **Cohere** (Embed→Rerank→
Chat reference stack); **Mem** (auto cross-linking).

Top patterns to adopt:

1. **Hybrid search + two-stage retrieve→rerank** (cheap recall to top-100/200,
   cross-encoder rerank to top-k); rank on more than similarity (blend
   recency/authority/popularity). chemclaw2 has hybrid RRF already; add the
   reranker and freshness signals.
2. **Onyx connector ABC** (`load_from_state` full + `poll_source(start,end)`
   incremental + resumable Postgres checkpoints; trust `modified` timestamps,
   fall back to content hashing) so only changed docs re-embed. Generalize the
   current `sync_worker`.
3. **Permission-aware retrieval, fail-closed** — sync source ACLs at index
   time, filter at retrieval time via SQL `WHERE`, never post-generation
   (aligns with chemclaw2's owner-scoping rules).
4. **Living wiki = generate-from-corpus with mandatory inline citations +
   staleness detection via citation re-verification** (Falconer): on each
   sync, re-check cited sources; changed/deleted source → mark claim stale →
   draft-and-refine regeneration with a human review gate (don't silently
   overwrite). Grounding citations double as freshness anchors.
5. **Background auto-cross-linking** (Mem) — embedding-similarity job suggests
   related-page backlinks as new content lands.
6. **Golden eval set + continuous eval** (retrieval recall@k / nDCG +
   generation faithfulness / citation-coverage / hallucination rate).

Sources: [Onyx](https://github.com/onyx-dot-app/onyx) ·
[Onyx connector contract](https://github.com/onyx-dot-app/onyx/blob/main/backend/onyx/connectors/README.md) ·
[Glean hybrid search](https://www.glean.com/blog/hybrid-vs-rag-vector) ·
[Falconer living documentation](https://falconer.com/guides/living-documentation) ·
[DeepWiki](https://docs.devin.ai/work-with-devin/deepwiki) ·
[Cohere Rerank](https://cohere.com/rerank) · [Mem (Zapier)](https://zapier.com/blog/mem-ai/).

---

## 5. Agent reliability, observability, eval & future-proofing

Grounded primarily in Anthropic's own engineering guidance for the Claude
Agent SDK, plus reputable LLMOps sources.

- **Reliability:** few, sharp, namespaced tools; token-bounded tool
  responses (<~25k tokens); tool descriptions prompt-engineered as carefully
  as the system prompt; **strict tool use + JSON structured outputs** (Pydantic
  + constrained decoding) to kill parse/param errors; **Citations API** for
  enforced grounding; layered hallucination guardrails (ground → cite →
  allow-IDK → verify); checkpoints before irreversible actions; subagents for
  context isolation.
- **Observability/eval:** self-host **Langfuse** (Postgres-friendly, OTLP
  ingest); instrument with **OpenTelemetry GenAI semantic conventions**
  (vendor-neutral, swappable backend); near-real-time token/cost from usage
  fields; **golden datasets + LLM-as-judge regression evals in CI**
  (deterministic checks first; validate the judge at 75-90% human agreement).
- **Flexibility/future-proofing:** keep tools behind **MCP** for model
  portability (chemclaw2's `mcp_molfp`/`mcp_rxnfp` are the right bet);
  config-driven model selection (no hardcoded model IDs); prefer upgrading SDK
  features over bespoke logic (compaction, sessions, hooks, skills — all in
  chemclaw2's anti-feature list already); version prompts + agent memory with a
  schema; progressive disclosure via Skills.
- **Security (already strong in chemclaw2):** redact PII/secrets at the tool
  boundary _before trace export_; fail closed on rate limiters/SSRF/role
  checks; resolve DNS once and bind the IP (TOCTOU); treat SDK auto-loaded
  paths (`.claude/`) as admin-write; least-privilege `allowed_tools` per
  surface.

Sources: [Writing effective tools for AI agents](https://www.anthropic.com/engineering/writing-tools-for-agents) ·
[Building effective agents](https://www.anthropic.com/research/building-effective-agents) ·
[Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) ·
[Citations API](https://www.anthropic.com/news/introducing-citations-api) ·
[Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview) ·
[Langfuse vs Phoenix/Arize](https://langfuse.com/faq/all/best-phoenix-arize-alternatives) ·
[OTel GenAI conventions](https://uptrace.dev/blog/opentelemetry-ai-systems).

---

## 6. RDKit / pgvector / DRFP implementation notes

- **Use `bit(N)`, not `vector`,** for fingerprints (`vector`-with-index caps at
  2000 dims; `bit` goes to 64000). chemclaw2 already uses `bit(2048)` — correct.
- **`bit_jaccard_ops` (`<%>`), not `bit_hamming_ops` (`<~>`),** for chemistry
  similarity — the §1 fix.
- **HNSW over IVFFlat** for a search service (better recall/latency, no
  training step). Tune `hnsw.ef_search` at query time; build with
  `CREATE INDEX CONCURRENTLY` on populated tables.
- **The cartridge does not fingerprint reactions** — DRFP stays in the
  bit-column/pgvector lane regardless of any cartridge adoption for molecules.
- **`lwreg` (rinikerlab)** is the canonical open-source RDKit+Postgres
  registration+search reference and pairs with the cartridge; worth studying
  before building registration.

Sources: [pgvector README](https://github.com/pgvector/pgvector) ·
[pgvector HNSW vs IVFFlat (Google Cloud)](https://cloud.google.com/blog/products/databases/faster-similarity-search-performance-with-pgvector-indexes) ·
[lwreg repo](https://github.com/rinikerlab/lightweight-registration) ·
[DRFP repo](https://github.com/reymond-group/drfp).

---

## Prioritized roadmap (leverage × cost)

| # | Item | Surface | Leverage | Cost | Blocker |
|---|------|---------|----------|------|---------|
| ✅ | Fingerprint metric fix (Hamming→Jaccard) | chem-search | High | Low | — (shipped) |
| 1 | Standardize-on-register + InChIKey dedup | chem-registry | High | Med | — |
| 2 | LitQA2-style eval + LLM-judge in CI | agent/eval | High | Med | — |
| 3 | RDKit cartridge substructure search | chem-search | High | Med | needs PG extension |
| 4 | Two-stage retrieve→rerank + freshness | retrieval | Med-High | Med | — |
| 5 | Falconer staleness loop (citation re-verify) | wiki | Med-High | Med | — |
| 6 | Langfuse + OTel GenAI tracing | observability | Med | Med | — |
| 7 | Strict tool use + structured outputs | agent | Med | Low | quick win |
| 8 | R-group / SAR tables | chem-search | Med | Low-Med | — |
| 9 | ORD-style reactions + substructure | chem-search | Med | Med | — |
| 10 | Onyx-style incremental connectors | ingestion | Med | Med | — |

Each item is tracked as an area-prefixed bullet in `BACKLOG.md` under
"Competitive / deep-research analysis (2026-05-29)".
