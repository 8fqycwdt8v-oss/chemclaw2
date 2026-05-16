hemClaw v2 — Founding document
A single document capturing what chemclaw2 does for users, how it's built, and what we explicitly don't build. Optimized for a 2–3 engineer team operating and evolving the system over a 3-to-5-year horizon.

1. Vision
chemclaw2 is a knowledge-intelligence agent for pharma R&D. Three surfaces compose it:

A conversational agent that answers chemistry questions, plans syntheses, runs scientific tools, and triggers experiments — grounded in the org's accumulated knowledge.
A living knowledge wiki that synthesizes that knowledge into human-readable, citation-traced pages — the primary surface for consuming knowledge, with chat as the surface for performing tasks.
Chemistry-native search over compounds and reactions, by structure similarity and substructure — the primary surface for finding "what have we seen like this before."
Integrations with experimental data sources (ELN, LIMS, instruments) so new data flows in and triggers proactive investigation.
Everything else is in service of these.

2. Operating principles
The principles are constraints on engineering. If a proposed feature violates one, it doesn't ship in v1.

Off-the-shelf over self-built. Every capability must be backed by a maintained external library or framework. If it can't be, defer the capability.
Library-driven feature evolution. New features arrive primarily by upgrading dependencies — the Claude Agent SDK, MCP servers, RDKit, Tiptap, Next.js — not by writing more application code. Codebase >2x growth in 18 months = the team is writing too much.
Small team, long horizon. Operable by 2–3 engineers indefinitely.
Postgres-first. One database for state, sessions, audit, wiki pages, entity relationships, search, and chemistry-native fingerprints. Add specialized stores only when measured pain justifies them.
Defer until measured. No speculative infrastructure.
Vertical slices, not horizontal abstractions. Each capability is a thin slice. No internal frameworks.
Replaceable, not extensible. Bias to rip-and-replace over abstract-and-refactor.
3. User stories
The wiki is treated as a primary surface across personas, not a curator-only feature. Chemistry-native similarity search is treated as a primary capability across personas, not a power-user feature.

3.1 Process / synthesis chemist
Describe a target in natural language; receive proposed routes annotated with literature precedent.
Paste a reaction SMILES (or draw a reaction) and find every similar reaction we've run, ranked by structural similarity.
Read a synthesized wiki page about a transformation class before designing a new variant.
Ask "has anyone in our org made this transformation before?" and get canonical reactions with source experiments.
Request yield, byproduct, and failure-mode predictions before running a step.
Compare two proposed routes along cost, time, and risk.
Walk through the mechanism of a proposed step with the agent flagging concerns.
Ask "what's likely to break at scale" and get specific concerns grounded in prior data.
Design a high-throughput screening plate (96 / 384 / 1536 wells) from a candidate set so a parallel experiment can run in one batch.
Score the solvent choices in a proposed step against green-chemistry guides and see safer alternatives.
Run a statistical analysis on a set of prior reactions and get a ranked list of which features (catalyst, base, temperature, solvent) most predict yield.
Export a set of reactions to a standard interchange format so the wider community can consume them.
3.2 Medicinal / project chemist
Read the project's wiki page to see current SAR consensus, best-compound lineage, what's been ruled out.
Paste a structure and find structural neighbors in our compound database — across all projects (or scoped to mine) — ranked by similarity.
Search for compounds matching a substructure ("show me every compound we've made with this scaffold").
Pose SAR queries across a compound family with cited tabulated answers.
Propose a hypothetical analog; receive property predictions with calibrated uncertainty.
Request bioisosteric replacements with rationale.
Launch a small library design with property filters; receive a ranked candidate list.
Navigate from a chat citation directly to the underlying wiki page.
3.3 Analytical chemist
Submit a spectrum (MS / NMR / IR); receive candidate structures ranked by fit.
Read the project's wiki page on analytical methods before developing a new one.
Request chromatography conditions for a separation problem.
Validate a proposed structure against measured spectra with confidence assessment.
Pull all analytical datasets linked to a sample or batch.
Ask "what are the impurities in this crude" and get a grounded hypothesis (often informed by similar reactions in the database).
Predict the expected NMR spectrum of a proposed structure so the predicted peaks can be compared to a measurement.
3.4 Computational chemist
Request QM calculations (semi-empirical or DFT) without managing compute infrastructure.
Cluster a screen of N candidates by structural similarity to see the chemical-space coverage.
Screen N candidates against an objective; receive ranked output with calibrated uncertainty.
Request conformer ensembles, tautomer enumeration, or mechanism comparisons.
Publish a calculation result into a wiki page section so downstream users find it.
Run a one-off scripted computation in an isolated sandbox without first packaging it as a reusable tool.
3.5 R&D project lead
Open the project's wiki landing page and see a curated, auto-fresh summary of the program's state.
See the contradictions backlog for the project and triage it.
See which wiki pages have human edits, which are system-generated only, and which are stale.
Receive proactive notifications when something significant happens (a campaign converges, a foundational claim is invalidated, a new external compound or paper appears that's structurally similar to a project lead).
Set budget caps (cost, compute, experiments) per project / per user.
Require approval before the agent runs operations above a cost or risk threshold.
Compare two campaigns side-by-side.
Opt the project in or out of cross-project learning (anonymised motif patterns shared across projects).
See the agent's auto-proposed resolutions to contradictions in the project's knowledge, with the evidence backing each side.
See which prompts and skills the system is currently testing or has recently promoted, based on measured success rates over the last N runs.
3.6 Chemistry-native search (cross-persona)
As a chemist, I can search compounds by structural similarity (paste a structure, get the top-K most similar with similarity scores).
As a chemist, I can search compounds by substructure (paste a substructure, get every compound that contains it).
As a chemist, I can search reactions by reaction similarity (paste a reaction, get the top-K most similar reactions across our experiments and literature).
As a chemist, I can combine similarity search with metadata filters — "compounds similar to this, but only in project X, only with measured logP < 3."
As a chemist, I can ask the agent a question and have it transparently use similarity search as one of its tools — "did we ever see a yield like this in a similar transformation?"
As a chemist, every compound and reaction we store is automatically fingerprinted at ingestion; I never have to trigger this manually.
As a chemist, I can see which existing compounds are nearest neighbors to a proposed new analog before we synthesize it, so I can leverage prior measurements.
3.7 Knowledge wiki — reader and contributor (cross-persona)
Reading. Navigate to a synthesized page about any compound, project, campaign, document, transformation class, or topic. See freshness state, last-updated time, what changed, and which underlying facts triggered the regeneration. Follow citations through to source facts. Browse an index page. Search the wiki and get hits ranked alongside document, compound, and reaction hits. Subscribe to a page for change notifications.

Requesting. Request a page on a topic that doesn't exist yet. Request regeneration of a page believed stale. Ask "why hasn't this page been updated since X?"

Editing. Edit a synthesized page with a rich block editor (formatted text, tables, molecule structures, reaction diagrams, citation pills). Mark edits as authoritative so they survive future regenerations. Preview before publishing. See full revision history. Flag a page as "needs review." Mark a literature claim as expert-disputed. Archive an obsolete page (history preserved). Promote a page from exploratory to validated maturity.

Sharing. Permalink to a specific revision. Rich previews in chat / Teams / email. Land on a page anchor from a chat citation. Export as PDF or Markdown.

Research output. Run a "deep research" mode that traverses the knowledge graph across compounds, reactions, prior wiki pages, and documents and returns a structured report with inline citations as a saveable deliverable. Retrieve any document in the format the next step needs — rendered markdown, original PDF, or raw bytes.

3.8 Compliance / QA
Complete audit trail for any agent answer — sources used, tools called, confidence claimed.
Full revision history of any wiki page (system, human, triggering facts).
Verify sensitive identifiers were redacted before leaving the controlled perimeter.
Replay any agent decision and reproduce the answer from historical state.
See what the system knew at any past point (bi-temporal); reproduce a wiki page as of a date.
Verify role-based access controls — User A cannot see Project B's data or pages.
Look up hazard classification and GHS pictograms for any compound or CAS number before working with it.
Override the scheduled-substance gate with a documented justification for a legitimate research scenario, with the override recorded in the audit trail.
3.9 System administrator
Onboard a new tenant with isolated data, configuration, budgets, wiki namespace.
Configure settings at global / org / project / user scopes.
Toggle features per scope without deploying code.
See system health, tool availability, queue depth, fingerprinting backlog, wiki regeneration backlog.
Rotate auth keys without taking the system down.
Disable a tool or feature fleet-wide for incident response.
Manage redaction categories for outbound model traffic.
Configure per-tool authorization with deny / ask / allow rules scoped per user, project, or org-wide.
Run scheduled regression evaluations of the agent against a golden set of chemistry problems and see week-over-week score deltas.
3.10 Any chemist (cross-cutting)
Ask a question in natural language; receive a cited answer.
Receive wiki page and compound/reaction links in the answer so I can navigate to durable knowledge.
See exactly which sources were used.
See the confidence level of each claim.
Receive a clarifying question when the agent is uncertain.
Pause and approve before expensive or destructive operations.
Chat in long-running sessions that survive process restarts.
Receive proactive alerts when new data relevant to my project appears.
Watch the agent's live progress as it works.
Cancel a runaway operation cleanly.
Submit thumbs-up or thumbs-down feedback with a reason on any agent answer so the system improves over time.
Mark a successful interaction as a reusable skill the agent can invoke directly the next time the same kind of question arises.
See and manage the agent's todo list during a long-running investigation — mark items done, reorder, or skip steps.
Have the agent auto-detect when it is looping (calling the same tool with the same arguments repeatedly) and stop gracefully instead of burning the budget.
Preview the agent's multi-step execution plan before it runs and approve, edit, or reject individual steps.
3.11 Autonomous campaign owner
Start an automated optimization campaign with a stopping criterion.
System creates a campaign wiki page that updates as the campaign progresses.
Get notified when the campaign converges, dies, or hits a decision point.
Choose round-by-round approval or fully autonomous mode.
See per-round results in real time; intervene mid-flight.
Resume a paused campaign without losing state.
Review the auto-generated campaign wiki page at conclusion and edit it as the durable record.
3.12 External integrations (non-human user)
A downstream system (ELN, LIMS, SDMS) can push new data and trigger autonomous investigation, automatic fingerprinting, and wiki re-flagging.
A chat client can consume streaming responses through a stable API contract.
An external chat surface (Microsoft Copilot) can route queries with user identity preserved end-to-end.
3.13 Custom tooling and skill forging (cross-persona)
Describe a custom analysis or computation in natural language; the system synthesizes a tool, tests it against the example inputs you provide, and adds it to the available toolkit ready to invoke.
Browse the personal / project / org-wide catalog of custom tools available to me right now and see who authored each.
Promote a custom tool from personal scope to project or organisation scope after an admin sign-off, so collaborators can use it.
Receive a notification when a custom tool starts failing its scheduled validation tests, with a path to re-forge it or retire it cleanly.
4. Technical architecture
4.1 Stack
Layer	Technology	Why this choice
Agent runtime	Claude Agent SDK (TypeScript)	Hooks, sessions, MCP, plan mode, sub-agents, prompt caching shipped by Anthropic. Zero custom loop.
LLM	Anthropic models direct	No proxy. Prompt caching native.
Web app	Next.js (App Router) + React Server Components	Chat, wiki, admin in one app. ~2–3k LOC target.
Editor	Tiptap	Off-the-shelf block editor; Markdown-first; MIT.
Storage	Postgres	One database for everything. Hosted: Neon, Supabase, or RDS.
Keyword search	Postgres full-text search (tsvector)	Wiki + documents.
Vector search	pgvector extension	Wiki chunk embeddings, compound fingerprints, reaction fingerprints, all in one DB. HNSW indexes.
Molecule fingerprints	RDKit Morgan/ECFP4 (radius 2, 2048 bits) — generated via a small MCP server	Deterministic, no model, decades of validation in cheminformatics.
Reaction fingerprints	DRFP (Differential Reaction Fingerprints, 2048 bits, binary) — generated via a small MCP server	Deterministic, no model, published method (Schwaller et al.).
Tools	MCP servers	Mostly off-the-shelf; custom only for chemistry gaps.
Job queue / scheduled work	pg-boss (Postgres-backed)	Retries, scheduled jobs, async fingerprinting, durability — without operating a separate service.
Auth	Microsoft Entra ID (or Auth0 / Clerk)	Off-the-shelf SSO.
Observability	OpenTelemetry → Langfuse for traces; Better Stack or Axiom for logs	Both managed.
Hosting	Fly.io, Railway, or Render for the app; managed Postgres	No Kubernetes.
CI/CD	GitHub Actions	Standard.
4.2 Chemistry vectorization — the design
Two deterministic fingerprint families cover the v1 chemistry-search needs:

What	Method	Library	Storage	Similarity
Molecule	Morgan / ECFP4 circular fingerprint, radius 2, 2048-bit	RDKit (Python)	compounds.morgan_fp bit(2048) mirrored to pgvector as vector(2048) for HNSW indexing	Tanimoto on bit columns; cosine on vector column (Tanimoto-adjacent for nearest-neighbor ranking)
Reaction	DRFP differential reaction fingerprint, 2048-bit	drfp PyPI package	reactions.drfp_fp bit(2048) mirrored to pgvector as vector(2048)	Same
Substructure	RDKit SMARTS matching	RDKit (Python) via MCP tool	Computed at query time over filtered candidate set	n/a (binary match)
Why this stack:

Both methods are deterministic and library-shipped. No model training, no GPU, no embeddings infrastructure. RDKit + DRFP are both pip-installable.
Both produce bit vectors. Storage as bit(2048) enables native Tanimoto via SQL (bit_count(a & b)::float / bit_count(a | b)); mirror to pgvector(2048) for HNSW-indexed approximate nearest-neighbor queries when the table is large.
No new infrastructure. Fingerprinting happens in two small MCP servers (mcp-molfp, mcp-rxnfp) — each ~100 LOC of Python. Vectorization is async via pg-boss after row insert; users don't wait.
Substructure search is a separate, optional path. Most queries use similarity; substructure is a slower SMARTS-match path over a candidate set narrowed by metadata + fingerprint.
If pgvector ever proves insufficient (millions of compounds, demanding latency), the upgrade path is the RDKit Postgres cartridge (rdkit-postgres) — native chemistry types and operators in SQL. Defer until measured.

4.3 What the app code looks like
Target for v1: under 6,000 lines of application code, excluding dependencies.

One Next.js app: chat, wiki, admin, similarity-search routes. ~2.5–3k LOC.
A SessionStore adapter for the Claude SDK to use Postgres (~100 LOC).
Domain hooks for the SDK: redaction, fact-ID check, scheduled-substance gate. ~200 LOC.
Database schema + migrations (Drizzle or Prisma) including chemistry-fingerprint columns and HNSW indexes. ~600 LOC.
Two fingerprinting MCP servers (mcp-molfp Morgan/ECFP, mcp-rxnfp DRFP). ~100 LOC each.
Two search MCP tools or agent builtins (find_similar_molecules, find_similar_reactions, find_substructure_matches). ~150 LOC combined.
A pg-boss configuration with scheduled jobs and the post-insert fingerprinting queue. ~100 LOC.
Other domain MCP servers for chemistry tools the community hasn't shipped. ~500 LOC each, independent.
4.4 Architecture in one paragraph
A Next.js app embeds the Claude Agent SDK; the SDK's session store points at Postgres; the agent calls MCP tool servers, including two small fingerprinting servers that turn SMILES into Morgan/DRFP bit vectors; agent and human writes flow into Postgres (sessions, wiki pages, audit log, compound + reaction tables with fingerprint columns); chemistry-native similarity search runs in Postgres via pgvector HNSW indexes and bit Tanimoto; the wiki UI in the same Next.js app reads those tables and renders pages with Tiptap; pg-boss runs scheduled wiki regeneration, fingerprinting of newly-ingested compounds and reactions, and reanimator-style resumption; observability ships through Anthropic's OTel hooks into Langfuse. That is the whole system.

5. v1 scope vs. deferred
5.1 In v1 (off-the-shelf or trivial code)
Chat agent with grounded, cited answers.
Wiki: read, edit (Tiptap), search (Postgres FTS + pgvector), regenerate-on-demand.
Similarity search for compounds (Morgan/ECFP) via pgvector HNSW.
Similarity search for reactions (DRFP) via pgvector HNSW.
Substructure search for compounds (RDKit SMARTS via MCP tool).
Automatic fingerprinting of compounds and reactions on insert (via pg-boss job).
Vector embedding of wiki chunks for semantic search.
Tool calls via MCP (RDKit, fingerprinting, KG queries, document fetch, web search).
Session persistence (SDK + Postgres adapter).
Pause-for-clarification (SDK AskUserQuestion + defer).
Egress redaction (SDK pre-LLM hook).
Per-tenant RLS — only if >1 tenant at launch.
Audit log via Postgres triggers.
Langfuse observability.
Skills as filesystem markdown packs (all loaded, no maturity gating).
Approval flow for expensive operations via AskUserQuestion.
Bi-temporal columns (valid_from, valid_to) on entity tables.
5.2 Deferred (require custom code or new services)
Feature	Why deferred	Trigger to add it
Neo4j / dedicated graph DB	Postgres + foreign keys handle entity relationships	≥3 measured graph-traversal queries that can't be expressed in SQL
Temporal / Restate / durable workflow engine	SDK defer + pg-boss covers v1	≥2 distinct multi-day autonomous workflow types
RDKit Postgres cartridge (rdkit-postgres)	pgvector + fingerprinting MCPs is portable across managed Postgres providers; the cartridge requires self-hosted Postgres	Compound table grows past ~1M rows and pgvector latency becomes an issue, or substructure search load justifies it
Learned chemistry embeddings (ChemBERTa, MolFormer, rxnfp)	Morgan + DRFP are deterministic and cheap; learned embeddings need a model server and GPU	Demonstrated semantic search failure modes that fingerprints don't catch
DSPy GEPA prompt optimization	Iterate prompts by hand	Prompt iteration becomes the measured bottleneck
Skill maturity tiers + promotion	All skills are equal	Skills produce wildly different success rates and need gating
Tool forging	Author tools by hand	Manual authoring becomes the bottleneck
Cross-model confidence ensemble	Single LLM-self-rated confidence	Demonstrated failure mode a second model would catch
Reanimator daemon	SDK defer handles pause/resume	Sessions stall in ways defer doesn't recover
Plan-mode preview (HTTP approve/reject)	AskUserQuestion at decision points	Users ask for full upfront plan approval as a workflow
Wiki auto-regeneration daemon	Regenerate on read or on event	On-demand creates measurable stale complaints
Wiki contradiction auto-detection	Manual curator flagging	Backlog grows faster than humans can find them
Realtime collaborative editing	Single-editor optimistic locking	Multi-editor conflicts hit often enough to matter
Multi-provider LLM routing	Anthropic only	A feature lands on another provider that Anthropic doesn't match
Sub-agent dispatch with restricted tool sets	SDK's built-in covers it	Workloads need different sub-agent contracts
Knowledge wiki maturity tiers	All pages "current"	Curation overhead needs automated tiering
5.3 Anti-features
Custom ReAct loop.
Custom hook framework.
Custom MCP client.
LiteLLM or any LLM proxy.
Self-hosted Kubernetes.
Self-hosted observability stack.
Internal projector framework.
New ORM, migration framework, SQL builder.
New UI framework.
Bespoke wiki engine.
Custom molecule embedding model — RDKit Morgan is the v1 answer, full stop.
Custom reaction embedding model — DRFP is the v1 answer, full stop.
6. Evolution path
Feature growth comes from three sources, in order:

Anthropic ships SDK capabilities — chemclaw2 adopts by upgrading.
The MCP and chemistry ecosystems mature — community MCP servers replace or augment custom tools; new RDKit features, new fingerprint methods land as library upgrades.
A deferred item crosses its trigger condition — added as a vertical slice with the same constraint: off-the-shelf or trivial.
If the team finds itself designing a system rather than adopting one, the right answer is usually to defer.

7. What chemclaw2 is not
Not GxP-certified. GxP-adjacent only.
Not multi-region. Single deployment per tenant.
Not a real-time collaboration product. Optimistic locking on wiki edits.
Not a replacement for ELN, LIMS, or commercial chemistry suites.
Not a chat product. The wiki is the primary persistent surface.
Not an agent framework. We consume Anthropic's.
Not a low-latency / high-QPS system. Hundreds of users, not millions.
Not a chemistry-embedding-research project. Morgan + DRFP are the answer; we are not training new embedding models.
8. Success metrics for the engineering choices
The architecture is succeeding if, after 12 months in production:

Team still 2–3 engineers.
Application code grown by less than 50%.
Number of operated services unchanged or smaller.
New capabilities mostly shipping by upgrading dependencies.
The team has been able to take vacations.
9. First-90-days plan
Weeks 1–2. Next.js + Postgres (with pgvector extension enabled) + Auth + Claude Agent SDK. One /api/chat route. Session persistence via SDK Postgres adapter. Langfuse wired. Hosted on Fly.io.

Weeks 3–4. Compound and reaction tables with bit(2048) fingerprint columns + pgvector mirror columns + HNSW indexes. Two MCP servers: mcp-molfp (RDKit Morgan/ECFP) and mcp-rxnfp (DRFP). pg-boss worker that fingerprints rows on insert. Three agent tools: find_similar_molecules, find_similar_reactions, find_substructure_matches.

Weeks 5–6. Wiki: Postgres articles + revisions + citations tables. Tiptap editor in Next.js. Read / edit / list / search routes. Citation pills as a Tiptap extension. Wiki chunk embeddings stored in pgvector for semantic wiki search.

Weeks 7–8. Additional MCP tools: KG queries against Postgres entity tables, document fetch + search, web search. Three domain hooks: redaction, fact-ID check, scheduled-substance gate.

Weeks 9–10. Synthesis-campaign skill: kick-off a campaign, agent dispatches experiments, generates a wiki campaign page, uses AskUserQuestion at decision points. pg-boss for retries.

Weeks 11–12. Compliance: audit log triggers, RLS if >1 tenant, redaction unit tests, replay tooling. First external integration (ELN read path with auto-fingerprinting of incoming compounds and reactions). Microsoft Copilot connector if needed.

End of 90 days: working chemclaw2 in production with 1–3 pilot users. Under 6k LOC of application code. Chemistry-native similarity search live from day one. One-engineer ops.