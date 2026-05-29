# Full-codebase code review — May 2026

A super-detailed, recall-biased audit of the chemclaw2 backend, partitioned
into eight risk domains. Each domain was reviewed at high effort against
general correctness/security angles plus the specific CLAUDE.md rules for that
surface. Findings were verified against the source before action.

- **Scope:** the codebase at `main` @ `70d4124`.
- **Method:** domain-partitioned deep review (cloud `ultra` was unavailable in
  the environment; ran as high-effort local multi-agent passes scoped per
  domain). The pytest suite needs a live Postgres + app deps, neither present
  locally — `ruff` + `mypy` + `check_migrations` ran clean here; the full suite
  runs in CI.
- **Outcome:** no unauthenticated RCE / data-exfiltration blockers. One real
  cross-user write (fixed). Several HIGH items are either policy decisions or
  multi-process/OOM hardening that need a live environment to verify — fixed
  the contained ones, deferred the rest to `BACKLOG.md`.

## Severity legend

`blocker` ship-stopper · `high` exploitable/data-corrupting on a realistic path ·
`medium` reachable but bounded · `low` edge-case / drift / cleanup.

## Fixed in the audit PR

| Domain | Severity | Finding | Fix |
|---|---|---|---|
| Secrets | high | `sk-proj-…` / `sk-svcacct-…` OpenAI keys never redacted — the generic `(sk\|rk\|pk)[-_][A-Za-z0-9]{20,}` body stops at the `proj` hyphen, so live keys passed the tool boundary. | Broadened the generic charset to allow internal `-`/`_`; regression test `_OPENAI_PROJ`. |
| Secrets | high | `_redact_obj` didn't traverse tuples/sets and never redacted dict **keys**; no depth/cycle bound (a deep input → `RecursionError` → caught by the fail-open hook → raw input through). | Added tuple/set traversal, key redaction, and a depth cap (`_REDACT_MAX_DEPTH`). |
| Secrets/substance | high | Pre-tool hook's outer `except` returned `{}` (allow with raw input) — a failure in the controlled-substance block or redaction disabled **both** security gates. | Fail closed: the outer `except` now returns a `block` decision. |
| Substance | low→sec | Zero-width strip used a small explicit char-class, missing bidi marks (U+200E/200F) and invisible math ops (U+2061) that defeat the `\b`-anchored regex. | `_normalize` now strips every Unicode `Cf`-category char; pure-unit regression added. |
| Owner-scoping | high | `confirm_synthesis_plan` discarded the owner-scoped `update_campaign_status` result, then `add_campaign_step` (no owner predicate) inserted against the client-supplied `campaign_id` — a forged id injected steps into **another user's** campaign (FK ≠ access control). | `update_campaign_status` now returns `bool`; the tool fails closed (returns an error, rolls back) when no owned/transitionable row matched. Regression tests added. |
| State machine | high | Campaign-completion `create_notification` + wiki + `updates` fired unconditionally whenever a campaign was observed all-complete, even when `system_advance_campaign` no-op'd (already terminal / concurrent worker) → duplicate completion notifications. | `system_advance_campaign` now returns `bool`; completion side effects are gated on the actual transition. |
| Workers | high | `fp_worker` SIGKILL path had no post-kill `proc.wait()` — the killed MCP child was never reaped, leaking zombies + open pipe fds across repeated timeouts. | Added a bounded reap after `proc.kill()` (mirrors the `mcp_codesandbox` pattern). |
| Workers | medium | `_cycle` was incremented only on the success path, so a persistently failing cycle pinned the counter — starving the heartbeat and re-running the heavy `%5`/`%60` periodic passes every tick. | Moved `_cycle += 1` + heartbeat into `finally`. |
| Observability | low | `connection.py` comment claimed the engine/session singletons are "created once at import time"; they're `None` until `init_db()` runs (correctly, so import never fails on a missing `DATABASE_URL`). | Corrected the comment to prevent a future regression that moves the env read to import scope. |

## Deferred (tracked in `BACKLOG.md`)

The headline deferrals, by severity:

- **~~HIGH — substance-gate self-approval (policy)~~ — RESOLVED:** product
  decision (2026-05-29) is to **keep the override self-service with an audit
  trail** — any user may proceed past the gate with a justification string,
  which `record_override` logs. Intentional "log-and-allow with accountability",
  not a bug; left as-is.
- **HIGH — worker double-execution:** no atomic step-claim; real under
  `--workers 2` + in-process worker. Wire the existing `running` status into a
  `FOR UPDATE SKIP LOCKED`/claim transition. Needs multi-process DB to verify.
- **HIGH — codesandbox OOM:** stdout "cap" is display-only; `communicate()`
  buffers the child's full output into the parent first. Needs a streaming-read
  rewrite + local sandbox testing.
- **MEDIUM:** substance multi-turn/lexicon evasion · X-Request-ID missing on
  error responses · readiness ignores campaign backlog · svc-token replay window
  · codesandbox RLIMIT-on-launcher.
- **LOW / drift / cleanup:** JWKS-URL startup guard · algo-confusion log ·
  NAT64/IPv4-mapped SSRF · web_search pinning · relative-redirect port drop ·
  SSN dashed-only · empty-id rate bucket · 422-vs-None justification · JWKS TTL
  wrapper · dead `CancelledError` handlers · fp_worker stderr log level.

See `BACKLOG.md` → "Full-codebase audit (May 2026)" for the full list with file
references and recommended fixes.

## Domain health notes

- **Auth:** solid, fail-closed throughout. JWKS/rate-limit/svc-token error paths
  deny and log; mock-auth gated at startup + per-request; all admin routes carry
  `get_admin_user` with the admin check before rate-limit. Residual items are
  low/medium.
- **SSRF:** a genuinely correct resolve-once-then-pin implementation — every
  A/AAAA record checked, redirects re-validated + re-pinned with
  `follow_redirects=False`, SNI/Host preserved so TLS still binds the real
  hostname, errors redacted. Only IPv6 NAT64 embedding and two fidelity nits
  remain.
- **Secrets/substance:** redaction is centralized and never logs the secret;
  the fixes above closed the real false-negatives and the fail-open path.
- **Owner-scoping:** strong across the queries layer — every per-user
  UPDATE/DELETE repeats `user_id`/`created_by`, all casts are `CAST(:x AS type)`
  (no `:x::type` misuse anywhere). The one real gap (campaign step insert) is
  fixed.
- **Transactions:** source-state predicates and `async with session.begin()`
  wrapping are correctly applied on the user-facing transitions; the worker
  completion idempotency is fixed, atomic step-claim deferred.
- **Rate-limit/budgets:** the limiter fails closed, uses an atomic
  `INSERT … ON CONFLICT … RETURNING` (no check-then-act), and hex-escapes keys;
  `try_consume_tool_call` is atomic. Budgets fail open by design (documented,
  logged) — an availability-over-enforcement choice, distinct from the limiter.
- **MCP/workers:** `mcp_codesandbox`'s timeout/reaper is the gold-standard kill
  pattern; no `shell=True` anywhere; both workers log startup/heartbeat/shutdown
  and guard reentrancy. Fixed the one place (fp_worker) that skipped the reap;
  the stdout-buffering OOM is deferred.
- **Observability/config:** `_JsonFormatter` uses real UTC ms timestamps (no
  `%f` trap); the CORS prod-gate correctly rejects `*`/localhost without
  substring over-match; request-id contextvar lifecycle is sound.
