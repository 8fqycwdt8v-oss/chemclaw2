# Architecture overview

chemclaw2 is one Postgres-backed FastAPI service that serves three
surfaces: a conversational chat agent, a living wiki, and a chemistry-
native search index. The agent runtime calls out to six stdio MCP
subprocess servers for chemistry-specific computations (RDKit Morgan
fingerprints, DRFP reaction fingerprints, RDKit retrosynthesis, a code
sandbox, a tabular-data tool, and chemistry-intelligence primitives).

```
                                                       ┌─────────────────┐
                                                       │   PostgreSQL    │
                                                       │   + pgvector    │
                                                       │                 │
                                                       │  state          │
                                                       │  sessions       │
                                                       │  wiki + chunks  │
                                                       │  audit log      │
                                                       │  rate limits    │
                                                       │  fingerprints   │
                                                       │  embeddings     │
                                                       └────────┬────────┘
                                                                │
                              ┌─────────────────────────────────┴──────────┐
                              │                                            │
   ┌──────────────────────────▼───────────────────┐    ┌───────────────────▼──────────────┐
   │                  FastAPI app                  │    │           Background workers       │
   │                                                │    │                                    │
   │  RequestIdMiddleware  →  CORSMiddleware  →     │    │  fp_worker (RDKit/DRFP via MCP)    │
   │  routes/                                       │    │  campaign_worker                    │
   │      chat (SSE)   wiki     campaigns           │    │                                    │
   │      search      todos    notifications        │    │  Postgres advisory locks +         │
   │      curator     admin    integrations         │    │  asyncio polling. Heartbeat log    │
   │      budgets     feedback                      │    │  every cycle.                      │
   │           │                                    │    └───────────────────────────────────┘
   │  queries/  ◄─── only layer importing SQLAlchemy
   │           │                                    │
   │  agent runtime                                 │
   │      Claude Agent SDK  +  Anthropic SDK        │
   │      ↳ tools.py / tools_chem.py /              │
   │        tools_campaign.py / ...                 │
   │      ↳ hooks.py (substance gate + secret       │
   │        redaction + zero-width normalisation)   │
   └────────────┬───────────────────────────────────┘
                │
                ▼ stdio subprocess (one per call, cached config)
   ┌──────────────────────────────────────────────────────────────────────────┐
   │                              MCP servers                                  │
   │  mcp_molfp            (RDKit Morgan / ECFP4 / property descriptors)       │
   │  mcp_rxnfp            (DRFP reaction fingerprints)                         │
   │  mcp_retrosynth       (RDKit single-step disconnection templates)         │
   │  mcp_codesandbox      (bwrap/unshare isolation tiers)                     │
   │  mcp_tabular          (pandas profiling + sklearn baselines)              │
   │  mcp_chem_intel       (SAscore, reaction classes, ORD validation)         │
   └──────────────────────────────────────────────────────────────────────────┘
```

## Layering rules (CLAUDE.md, hard)

- **Only `api/db/queries/*` imports SQLAlchemy primitives.** Routes,
  agent tools, hooks, and workers call exported async query functions.
- **Multi-step state transitions are wrapped in `async with
  session.begin()`.** Status flip + dependent inserts commit together.
- **Every per-user write predicates on `user_id` in `WHERE`.** FK
  constraints are not access control.
- **All outbound HTTP goes through `_fetch_validated`** (SSRF
  guard + DNS-rebinding pin). New external paths copy the pattern
  in `api/agent/tool_helpers.py`.

## Why no queue service / LLM proxy / ORM-on-top-of-the-ORM

- **Workers**: Postgres advisory locks + asyncio polling. One database;
  no Redis/Celery to deploy or babysit.
- **LLM proxy**: the Claude Agent SDK is a thin wrapper around the
  Anthropic SDK. LiteLLM-style proxies add a hop that has to be
  monitored, scaled, and updated for every model release.
- **Wiki engine**: Postgres FTS + pgvector embeddings. The "engine" is
  three query functions in `api/db/queries/wiki_*.py`.

## Cross-cutting concerns

| Concern        | Where                                           |
|----------------|-------------------------------------------------|
| Request ID     | `api/observability/middleware.py` + contextvars |
| Structured log | `api/observability/logging.py` (LOG_FORMAT)     |
| Rate limiting  | `api/db/queries/rate_limit.py` factory          |
| Auth           | `api/auth.py` (Entra ID JWT + optional service tokens) |
| Substance gate | `api/agent/hooks.py` (chat path only)           |
| Secret scrub   | `api/agent/hooks.py` `_SECRET_PATTERNS`         |
| SSRF guard     | `api/agent/tool_helpers.py` `_fetch_validated`  |
| Audit log      | `api/db/queries/audit.py` (admin writes, deletions) |
| CORS gate      | `api/main.py` `create_app` (prod-only fail-close) |
