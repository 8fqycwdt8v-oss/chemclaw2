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

- **Validate external input with a schema library.** No hand-rolled `typeof`/`instanceof` chains for nested object shapes — they drift and copy.
- **Implement type guards as `XSchema.safeParse(v).success`, not hand-rolled walkers.** Tiptap, Clerk, and request bodies all have Zod schemas — reuse them.
- **Branch on Zod issue `code` (`'too_big'`, `'invalid_type'`), not on `issue.message` text.** Message strings drift; codes are stable API.
- **Extract on the third copy.** Same 3+ line block in three sibling files = a helper. Applies equally to route preludes, error formatters, custom types, and shared validators.
- **Filter before map.** Don't materialize values you're about to discard.
- **No defensive checks the language guarantees.** `Number.isFinite` after `parseInt`, `x ? true : false`, double-casts through primitives (`str(int(b))` on numpy 0/1) — drop them.
- **Rate-limit responses always carry `Retry-After`.** Route 429s through the shared gate so the header is never missed.
- **Next.js route handlers gate auth + rate-limit through one helper** (e.g. `requireUserWithRateLimit` in `apps/web/lib/api-gate.ts`). No inline `auth()` → `rateLimit()` → 401/429 prelude per route.
- **Zod request body schemas live in `*-schemas.ts` modules** alongside a shared error formatter — route handlers `safeParse` the raw body and translate failures through it. No hand-rolled body validation inside the handler.
- **Every new `app/api/**/route.ts` handler ships with a vitest covering auth-fail, validation-fail, and happy paths;** webhook routes additionally need a signature-fail test.
- **Read env vars through `webEnv()` (`apps/web/lib/env.ts`) or `dbEnv()` (`packages/db/src/env.ts`), never `process.env.*` in routes, queries, or lib helpers.**
- **Use the env-var name the library expects** (e.g. `CLERK_WEBHOOK_SIGNING_SECRET`, `OPENAI_API_KEY`, `DATABASE_URL`); aliases break libraries that auto-read.
- **Module-load env reads (`const X = webEnv().Y` at file top) may only touch fields with defaults in the Zod schema;** required vars go inside a handler/factory so a missing var fails at request time, not build time.
- **Before creating `packages/db/migrations/NNNN_*.sql`, run `git fetch origin main && ls packages/db/migrations/` and pick a number higher than every file on `main`.** Branches off a stale base silently collide on the slot and on `_journal.json`.
- **Drizzle `customType` factories live in one shared module per package** (e.g. `packages/db/src/schema/custom-types.ts`). Don't redefine column type factories inside individual schema files.
- **All server-side logging goes through `@chemclaw2/observability` `logger`.** No `console.*` calls in app/worker code; pass `err` as the third arg and the logger handles stringification.
- **Wrap latency-critical work in `withSpan(name, attrs, fn)`** from `@chemclaw2/observability`. Don't call OTel `trace.getActiveSpan()?.addEvent()` directly for spans that should be measurable on their own.
- **Security gates fail closed.** A `.catch` on a permissions or authz lookup returns the deny verdict, never the allow path — document any intentional fail-open next to the call site (the `getBudget` cache is the template).
- **Owner-scope every per-user write.** Every `db.update` / `db.delete` against per-user data includes `eq(userId)` in WHERE — `sessionId` / `pageId` / `campaignId` alone is not enough; mirror the WHERE from the read that gated the write.
- **Wrap multi-step state transitions in `db.transaction`.** A status flip plus its dependent inserts/updates commit together or roll back together. `await updateStatus(...); for (...) await insertChild(...)` is this rule firing.
- **Repeat the source-state predicate on every transition UPDATE.** When a sibling function has `notInArray([terminal])` in WHERE, the new one needs it too — call sites can't be trusted to never invoke it post-completion.
- **Don't short-circuit security checks on identifier equality.** Same hostname ≠ same IP; same id ≠ same trust level. Re-validate DNS, ownership, and signatures on every redirect / hop / retry.
- **Guard `setInterval` polls against reentrancy.** An `inFlight` flag set on entry, cleared in `finally`; slow networks make poll N+1 fire before N's POST returns.
- **Escalate child-process kills SIGTERM → SIGKILL.** Add an `.unref()`'d backstop timer (~2 s per-call, ~5 s shutdown). Python-in-C children (RDKit, DRFP) ignore SIGTERM until the C frame returns and leak as zombies otherwise.
- **Enforce size invariants at the push site, not in fallback branches.** A helper that appends to a result is responsible for bounding its inputs; don't rely on a conditional branch at the call site to handle the oversized case.
- **Every devDep needs a matching script invocation in its own `package.json`;** when you remove a script, sweep the devDeps that only existed for it.
- **Before adding a new package, check whether an existing dep re-exports the feature** — `verifyWebhook` lives at `@clerk/nextjs/webhooks`, `SYSTEM_PROMPT_DYNAMIC_BOUNDARY` ships with `@anthropic-ai/claude-agent-sdk`.
- **Every `app/api/**/route.ts` handler is wired through `withRoute` / `withRouteParams` (`apps/web/lib/api-gate.ts`).** The wrapper owns auth, rate-limit, JSON parsing, Zod body validation, the `errorResponse` envelope, and `withApiContext` for request-id propagation — all from one config object. Don't inline pieces of the prelude; if `withRoute` is missing what your route needs, extend the wrapper instead of bypassing it.
- **Only `packages/db/src/queries/*` imports drizzle-orm primitives.** Routes, agent tools, hooks, and workers call exported query functions. Importing `db`, `sql`, `eq`, `inArray`, or schema tables from `@chemclaw2/db` outside the queries folder bypasses the layer that owns transactions, owner-scoping, and source-state predicates — if the query you need doesn't exist, add it.
- **Multi-step orchestration with compensating rollback lives in the queries layer, not the route.** A claim → validate → upsert → link sequence with try/catch + rollback at every failure site is one query function (template: `applyProposedEdit`). The route supplies validators and the publish closure; the query owns the state machine.
- **All API error returns go through `errorResponse(message, status, extras?, headers?)`.** Routes that need richer envelopes (override hints, dispute reasons) attach them via `extras` — no bespoke `NextResponse.json({ error, my_custom_field })` shapes. One envelope, one place to add cross-cutting fields.
- **Constants that mirror DB schema (vector dimensions, enum values, column length caps) live in `packages/db` and are re-exported by sibling packages that need them.** `EMBED_DIM` belongs next to the `vector(1536)` columns in `packages/db/src/embedding-constants.ts`; `agent-tools` re-exports. "Mirrors X in the other package" comments are a regression signal — fold the duplicate.
- **Each pluggable surface (agent tools, sub-agent allow-lists, experiment classification) reads from one registry module.** Adding a new tool is a single edit to `apps/web/lib/tool-registry.ts`, not three coordinated edits across `sdk-tools.ts` + `agent.ts` + a hardcoded subagent whitelist. Silent "forgot to register" failures aren't caught by typecheck, so don't create surfaces where they can hide.
- **Split a queries module past ~400 lines, and lift hook/callback bodies > ~100 lines out of the assembly file.** `wiki-chunks` / `wiki-search` / `wiki-citations` is the pattern: one concern per file, re-export from the original module if back-compat matters. Hooks extract to `<module>-hooks.ts` exporting `buildHooks(deps)` so the assembly file stays config-shaped and the bodies become unit-testable in isolation.
- **After resolving merge conflicts, run `git status` and verify every "modified" file is staged before `git commit`.** `git add <conflicted-path>` doesn't catch files you edited while resolving (e.g. porting an improvement into a moved module). Unstaged modifications ship as a silent regression in the merge commit — caught only by re-reading the diff against `origin/main` post-merge.

## Observability rules

1. Use `@chemclaw2/observability`'s `logger`; never bare `console.*`. Trace IDs and request context attach automatically.
2. Every `catch` either logs or rethrows. A swallowed error is a production blind spot.
3. Log 401, 429, 4xx denials, and security-hook blocks at info/warn before returning. Denials are signals, not noise.
4. Wrap every external boundary call (DB, MCP, `fetch`, embeddings) with timing + outcome. The log line is often the only diagnostic.
5. A fail-open path must emit an error before returning the fallback. Silent fail-open ≡ no enforcement.
6. Health endpoints reflect downstream state. Don't return 200 to unauthenticated probes while a critical dependency is down.
7. Post-response background work (onResult callbacks, fire-and-forget writes) logs its own outcomes. No caller will surface its failure.
8. Workers emit startup, heartbeat, and shutdown events; cron sweeps log what they deleted. Silence is not healthy.

### Error handling

- **Every `.catch()` and `catch {}` must log the error.** Server-side use `@chemclaw2/observability`'s `logger.error('event_name', {fields}, err)`; client-side use `console.error`. `.catch(() => 0)` / `.catch(() => [])` / `.catch(() => null)` without a log is indistinguishable from a genuine empty result.
- **Narrow caught errors with `err instanceof Error ? err.message : String(err)`. Never `(err as Error).message`.** Non-Error rejections (string throws, third-party SDKs rejecting with `{code, message}`) crash with `TypeError` or print `undefined`.
- **Guard `.returning()` destructuring before dereferencing.** After `const [row] = await db.insert(...).returning(...)`, write `if (!row) throw new Error('...: insert returned no row')` before touching `row.<field>`. Drizzle returns `[]` on RLS / schema mismatch / race-on-conflict failures.
- **In pg-boss worker handlers, re-throw after logging the failure.** A swallowed exception makes the queue mark a failed job as done, so retry policy never fires and the row stalls forever.
- **Wrap async `setInterval`/`setTimeout` callbacks in `try/catch`, not `.catch()` on the returned promise.** Sync throws before the first `await` slip past `.catch()` and become process-killing unhandled rejections (Node ≥15).
- **Guard child-process cleanup methods with `try/catch`.** `proc.kill()`, `proc.stdin.end()`, `reader.cancel()` all throw on already-closed handles, and a throw inside a settle/timer callback orphans the surrounding promise.
- **When listening to both `'error'` and `'close'` on a child process, short-circuit `'close'` if `'error'` already fired.** Set an `errorSeen` flag in the error handler. Otherwise a `close` racing ahead of `error` masks the real cause (ENOENT, EACCES, AbortError) behind a generic `exited with code N`.
- **Best-effort persistence wrappers must signal success/failure to callers.** Return `{ ok, error? }`, not `Promise<void>` — a void return makes "logged the failure and dropped it" look identical to "persisted".
- **In Next.js App Router, parse JSON as `await req.json()` inside `try/catch` — never `req.body ? await req.json() : {}`.** `req.body` is a stream; its truthiness doesn't tell you whether the body is safe to read.

## Stack

| Layer | Technology |
|---|---|
| Agent runtime | Claude Agent SDK (TypeScript) |
| LLM | Anthropic models direct |
| Web app | Next.js (App Router) + RSC |
| Editor | Tiptap |
| DB | Postgres (Neon/Supabase/RDS) + pgvector |
| Molecule fingerprints | RDKit Morgan/ECFP4 via MCP server |
| Reaction fingerprints | DRFP via MCP server |
| Job queue | pg-boss (Postgres-backed) |
| Auth | Microsoft Entra ID / Auth0 / Clerk |
| Observability | OpenTelemetry → Langfuse + Better Stack/Axiom |
| CI/CD | GitHub Actions |

## Operating principles (non-negotiable)

- Off-the-shelf over self-built. If a capability needs a custom-built backing library, defer it.
- Library-driven feature evolution. New features arrive by upgrading deps, not writing more app code.
- Postgres-first. One database for everything — state, sessions, wiki, audit, fingerprints, search.
- Defer until measured. No speculative infrastructure.
- Vertical slices. No internal frameworks. Replaceable, not extensible.
- Bias toward removal: every PR should question whether the surface it touches still earns its keep.

## Anti-features — never build these

Custom ReAct loop, custom hook framework, custom MCP client, LiteLLM/LLM proxy, self-hosted K8s, internal projector framework, bespoke wiki engine, custom molecule/reaction embedding models.
