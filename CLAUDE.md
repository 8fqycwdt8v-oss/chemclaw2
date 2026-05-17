# chemclaw2

Knowledge-intelligence agent for pharma R&D. Three surfaces: conversational agent, living wiki, chemistry-native search.

## Branch: typescript_old — PERMANENT ARCHIVE, DO NOT TOUCH

The `typescript_old` branch is a permanent, read-only archive of the original Next.js + TypeScript
monorepo. It **must never** be merged into `main`, rebased, force-pushed, or deleted. It exists
solely as a reference implementation of the original codebase before the Python backend migration.
No PRs should target this branch.

## Hard rules — non-negotiable, every session

1. **Commit before the session ends.** Uncommitted edits don't survive parallel sessions or branch switches.
2. **All merges to `main` via reviewed PR. No direct commits.**
   Flow: `gh pr create` → CI green (poll `gh pr checks`) → run `/review` and fix until clean → `gh pr merge <N> --merge` → delete remote branch (`git push origin --delete <branch>`) + local (`git branch -D`) + worktree if used.
   The user does NOT review PRs or trigger `/review`. An open PR is unfinished work.
3. **Define success criteria before starting; verify before claiming done.** Run the verification yourself.
   CI flakes: skip-list/xfail and note in `BACKLOG.md`. Stacked PRs: merge each as CI goes green, then `gh pr edit --base main`.

## Strongly recommended

- **Single worktree.** No `git worktree add` per agent/phase — parallel worktrees collide at merge. Branch sequentially off `main`.
- **Plan before code** for multi-step work: `superpowers:brainstorming` → `superpowers:writing-plans` → `superpowers:executing-plans`.
- Use `superpowers:finishing-a-development-branch` as the merge-and-cleanup checklist.

## General rules

1. Surface tradeoffs; don't paper over ambiguity.
2. Minimum code. No speculative abstractions, flags, or error handling for impossible cases.
3. Touch only what you must. Investigate unfamiliar state before deleting.
4. Log deferred work to `BACKLOG.md` — one bullet per item, prefixed by area, append-only.

## Stack

| Layer | Technology |
|---|---|
| Language | Python 3.12 |
| Agent runtime | Claude Agent SDK (Python) — `pip install claude-agent-sdk` |
| LLM | Anthropic models direct |
| API server | FastAPI + Uvicorn |
| ORM | SQLAlchemy 2.0 async + asyncpg |
| DB | Postgres + pgvector |
| Molecule fingerprints | RDKit Morgan/ECFP4 (in-process, no subprocess) |
| Reaction fingerprints | DRFP (in-process, no subprocess) |
| Job queue | asyncio polling + Postgres advisory locks |
| Auth | Clerk JWT via PyJWT + JWKS |
| Embeddings | OpenAI Python SDK (text-embedding-3-small) |
| Observability | OpenTelemetry |
| CI/CD | GitHub Actions |
| Frontend | Separate repo: chemclaw2_gui |

## Operating principles (non-negotiable)

- Off-the-shelf over self-built. If a capability needs a custom-built backing library, defer it.
- Library-driven feature evolution. New features arrive by upgrading deps, not writing more app code.
- Postgres-first. One database for everything — state, sessions, wiki, audit, fingerprints, search.
- Defer until measured. No speculative infrastructure.
- Vertical slices. No internal frameworks. Replaceable, not extensible.
- Target: <6,000 LOC application code at v1.

## Anti-features — never build these

Custom ReAct loop, custom hook framework, custom MCP client, LiteLLM/LLM proxy, self-hosted K8s, internal projector framework, bespoke wiki engine, custom molecule/reaction embedding models.
