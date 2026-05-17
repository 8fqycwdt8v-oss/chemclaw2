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

## Code rules — derived from past incidents

1. **Before creating `packages/db/migrations/NNNN_*.sql`, run `git fetch origin main && ls packages/db/migrations/` and pick a number higher than every file on `main`.** Branches off a stale base silently collide on the slot and on `_journal.json`.
2. **Parse every API route body through a Zod schema from `apps/web/lib/*-schemas.ts`** — never inline `typeof x === 'string'` or `as Foo` casts in `app/api/**/route.ts`.
3. **Read env vars through `webEnv()` (`apps/web/lib/env.ts`) or `dbEnv()` (`packages/db/src/env.ts`), never `process.env.*` in routes, queries, or lib helpers.**
4. **Use the env-var name the library expects** (e.g. `CLERK_WEBHOOK_SIGNING_SECRET`, `OPENAI_API_KEY`, `DATABASE_URL`); aliases break libraries that auto-read.
5. **Branch on Zod issue `code` (`'too_big'`, `'invalid_type'`), not on `issue.message` text.**
6. **Implement type guards as `XSchema.safeParse(v).success`, not hand-rolled walkers.** Tiptap, Clerk, and request bodies all have Zod schemas — reuse them.
7. **Every devDep needs a matching script invocation in its own `package.json`;** when you remove a script, sweep the devDeps that only existed for it.
8. **Before adding a new package, check whether an existing dep re-exports the feature** — `verifyWebhook` lives at `@clerk/nextjs/webhooks`, `SYSTEM_PROMPT_DYNAMIC_BOUNDARY` ships with `@anthropic-ai/claude-agent-sdk`, etc.
9. **Every new `app/api/**/route.ts` handler ships with a vitest covering auth-fail, validation-fail, and happy paths;** webhook routes additionally need a signature-fail test.
10. **Module-load env reads (`const X = webEnv().Y` at file top) may only touch fields with defaults in the Zod schema;** anything required goes inside a handler/factory so a missing var fails at request time, not build time.

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
