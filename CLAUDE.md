# chemclaw2

Knowledge-intelligence agent for pharma R&D. Three surfaces: conversational agent, living wiki, chemistry-native search.

## Hard rules — non-negotiable, every session

1. **Commit before the session ends.** Uncommitted edits don't survive parallel sessions or branch switches.
2. **After every task: PR → CI → merge, automatically. No direct commits to `main`, no waiting for user approval to ship.**
   Mandatory flow at the end of *every* task that touched code:
   1. Commit on the task branch and push (`git push -u origin <branch>`).
   2. Open a PR (`gh pr create`, or `mcp__github__create_pull_request` when `gh` is unavailable). Do not stop and ask.
   3. Poll CI until green (`gh pr checks --watch`, or `mcp__github__pull_request_read` with `method: "get_check_runs"` / `"get_status"`). Fix failures and push again — repeat until green.
   4. Run `/review` and fix until clean.
   5. Merge (`gh pr merge <N> --merge`, or `mcp__github__merge_pull_request`).
   6. Delete remote branch (`git push origin --delete <branch>`) + local (`git branch -D <branch>`) + worktree if used.
   The user does NOT review PRs, trigger `/review`, or click merge. An open PR or unmerged branch is unfinished work — keep going until step 6 is done. Only stop early if CI surfaces a failure you genuinely can't resolve, and report what's blocking.
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

## Code conventions

- **Validate external input with Pydantic.** No hand-rolled `isinstance`/`type()` chains for request bodies or nested objects — define a `BaseModel` and let Pydantic raise `422`.
- **Only `api/db/queries/*` imports SQLAlchemy primitives.** Routes, agent tools, hooks, and workers call exported async query functions. Importing `select`, `insert`, or model classes outside the queries layer bypasses owner-scoping and transaction logic — add the query function instead.
- **Wrap multi-step state transitions in `async with session.begin()`.** A status flip plus its dependent inserts commit together or roll back. Sequential awaits outside a transaction are this rule firing.
- **Repeat the source-state predicate on every transition UPDATE.** When one function excludes terminal statuses in WHERE, every sibling that touches the same state machine must do the same.
- **Owner-scope every per-user write.** Every `UPDATE`/`DELETE` against per-user rows includes `user_id = :uid` in WHERE — `session_id`/`page_id` alone is not access control.
- **Read env vars through `os.environ` inside factory/startup functions, never at module import time for required vars.** Module-level reads of required vars fail at import (killing all routes), not at request time. Vars with defaults are fine at module level.
- **Use the env-var name the library expects** (`DATABASE_URL`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`). Aliases break libraries that auto-read.
- **Before adding a migration, run `git fetch origin main && ls migrations/` and pick a number higher than every file on `main`.** Stale-base branches silently collide on the slot.
- **Extract on the third copy.** Same 3+ line block across three sibling files = a helper in `api/db/queries/` or a shared utility.
- **Filter before materialize.** Don't build a list you're about to discard.
- **No defensive checks the language guarantees.** `str(int(b))` on a value already known to be `int`, `x if x else None` on a value already `Optional` — drop them.
- **Security gates fail closed.** An `except` on a permissions or authz lookup returns the deny verdict, never the allow path — document any intentional fail-open next to the call site.
- **Don't short-circuit security checks on identifier equality.** Same hostname ≠ same IP. Re-validate DNS, ownership, and signatures on every redirect / hop / retry.
- **Guard asyncio polling loops against reentrancy.** An `in_flight` flag set on entry, cleared in `finally`; slow DB calls make the next poll fire before the previous one returns.
- **MCP subprocess kills: SIGTERM then SIGKILL.** Python-in-C children (RDKit, DRFP) may ignore SIGTERM until the C frame returns; add a 2 s backstop with `asyncio.wait_for`.
- **Split a queries module past ~400 lines.** One concern per file; re-export from the original if back-compat matters.
- **After resolving merge conflicts, `git status` to verify every modified file is staged before committing.** Unstaged resolutions ship as a silent regression.

## Observability rules

1. Use Python `logging` (`logger = logging.getLogger(__name__)`); never bare `print`. Log at appropriate levels.
2. Every `except` either logs or re-raises. A swallowed exception is a production blind spot.
3. Log 401, 429, 4xx denials, and security-hook blocks at info/warning before returning. Denials are signals, not noise.
4. Wrap every external boundary call (DB, MCP subprocess, httpx, embeddings) so failures are distinguishable in logs.
5. A fail-open path must log an error before returning the fallback. Silent fail-open ≡ no enforcement.
6. Health endpoints reflect downstream state.
7. Workers emit startup, heartbeat, and shutdown log events. Silence is not healthy.

### Error handling

- **Every `except` block must log the error or re-raise.** `except Exception: return []` without a log is indistinguishable from a genuine empty result.
- **Best-effort persistence wrappers must signal success/failure.** Return `{"ok": bool, "error": ...}`, not bare `None` — a `None` return makes "logged and dropped" look identical to "persisted".
- **Guard asyncio subprocess cleanup with `try/except`.** `proc.kill()`, `proc.stdin.close()`, `proc.communicate()` all raise on already-closed handles; a raise inside a `finally` block cancels the outer coroutine.
- **`asyncio.create_subprocess_exec`, not `subprocess.run` or `shell=True`, for MCP stdio.** No shell injection risk, compatible with the event loop.

## Security rules

1. **Rate limiters, SSRF guards, and role checks fail closed.** `pg_rate_limit`'s `except` returns `limited:True`; `_assert_not_private`'s DNS lookup raises on resolution failure. Fail-open at any of these is a security-relevant bypass — the only legitimate fail-open is a non-security budget cache, documented at the call site.
2. **Don't enable RLS without per-tenant predicates.** `ALTER TABLE … ENABLE ROW LEVEL SECURITY` with `USING (true)` is footgun, not policy. Either land real predicates or leave RLS off so the schema reflects reality.
3. **Anything the Agent SDK auto-loads is admin-write.** `.claude/skills/`, `.claude/settings.json`, and other SDK-discovery paths become prompt context for every user. Endpoints that write to those paths require admin auth.
4. **Don't echo internal error messages to clients.** Leaked MCP process paths, SQL fragments, and bearer-token prefixes in stack frames are OWASP A05. Surface a generic message; log the real error server-side.
5. **All outbound HTTP goes through `httpx.AsyncClient` with a pre-connect SSRF check (`_assert_not_private`).** A pre-flight `socket.getaddrinfo()` followed by separate `httpx.get()` leaves a DNS-rebinding TOCTOU window — resolve once and bind the resolved IP. The pattern in `api/agent/tools.py` is the template.
6. **Mutations on shared state require creator-or-admin; verify referenced ids before inserting rows that point at them.** FK constraints are not access control.
7. **Strip secrets and PII at the tool boundary.** `api/agent/hooks.py` pre-tool hook must catch API keys (`sk-…`, `pk_…`), bearer tokens, and SSNs — extend `_SECRET_PATTERNS`, not one-off scans.

## Stack

| Layer | Technology |
|---|---|
| Agent runtime | Claude Agent SDK (Python, `claude-agent-sdk`) |
| LLM | Anthropic models direct |
| Web framework | FastAPI + Uvicorn |
| DB ORM | SQLAlchemy 2.0 async + asyncpg |
| DB | Postgres (Neon/Supabase/RDS) + pgvector |
| Molecule fingerprints | RDKit Morgan/ECFP4 via MCP server (`mcp_molfp`) |
| Reaction fingerprints | DRFP via MCP server (`mcp_rxnfp`) |
| Job queue | asyncio polling worker + Postgres advisory locks |
| Auth | Clerk (PyJWT + JWKS) |
| Embeddings | OpenAI Python SDK (`text-embedding-3-small`) |
| CI/CD | GitHub Actions |

## Branch note

`typescript_old` is a permanent archive of the original Next.js/TypeScript monorepo.
**Never merge, rebase, or delete it.** Reference only.

## Operating principles (non-negotiable)

- Off-the-shelf over self-built. If a capability needs a custom-built backing library, defer it.
- Library-driven feature evolution. New features arrive by upgrading deps, not writing more app code.
- Postgres-first. One database for everything — state, sessions, wiki, audit, fingerprints, search.
- Defer until measured. No speculative infrastructure.
- Vertical slices. No internal frameworks. Replaceable, not extensible.
- Bias toward removal: every PR should question whether the surface it touches still earns its keep.

## Anti-features — never build these

Custom ReAct loop, custom hook framework, custom MCP client, LiteLLM/LLM proxy, self-hosted K8s, internal projector framework, bespoke wiki engine, custom molecule/reaction embedding models.
