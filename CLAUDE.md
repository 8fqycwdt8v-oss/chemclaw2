# chemclaw2

Knowledge-intelligence agent for pharma R&D. Three surfaces: conversational agent, living wiki, chemistry-native search.

## Hard rules — non-negotiable, every session

1. **Commit before the session ends.** Uncommitted edits don't survive parallel sessions or branch switches.
2. **After every task: PR → CI → merge, automatically. No direct commits to `main`, no waiting for user approval to ship.**
   Mandatory flow at the end of *every* task that touched code:
   1. Commit on the task branch and push (`git push -u origin <branch>`).
   2. Open a PR (`gh pr create`, or `mcp__github__create_pull_request` when `gh` is unavailable). Do not stop and ask.
   3. Poll CI until green (`gh pr checks --watch`, or `mcp__github__pull_request_read` with `method: "status"`). Fix failures and push again — repeat until green.
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
- Target: <6,000 LOC application code at v1.

## Anti-features — never build these

Custom ReAct loop, custom hook framework, custom MCP client, LiteLLM/LLM proxy, self-hosted K8s, internal projector framework, bespoke wiki engine, custom molecule/reaction embedding models.
