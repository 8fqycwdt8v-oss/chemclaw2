-- BACKLOG #39 + #37.
--
-- #39 — tool_permissions: app-layer validation in apps/web/app/api/admin/
-- tool-permissions/route.ts rejects malformed (scope, scope_id) pairs, but a
-- direct DB write (psql, future second writer, schema migration tool) bypasses
-- it. The shape rules are durable enough to encode as a CHECK so the database
-- itself refuses misconfigured rows:
--   scope='user'    → scope_id matches the Clerk user-id shape (user_...).
--   scope='project' → scope_id is anything that is NOT a user id and NOT the
--                     literal 'org' (free-form until a projects table exists).
--   scope='org'     → scope_id is the literal 'org' (the resolver hardcodes
--                     this lookup; any other value would silently never match).
-- Idempotent because this migration was missing from the drizzle journal
-- until 2026-05; some environments may have hand-applied it.
ALTER TABLE tool_permissions DROP CONSTRAINT IF EXISTS tool_permissions_scope_shape_chk;
ALTER TABLE tool_permissions
  ADD CONSTRAINT tool_permissions_scope_shape_chk
  CHECK (
    (scope = 'user'    AND scope_id ~ '^user_[A-Za-z0-9]+$')
    OR (scope = 'project' AND scope_id !~ '^user_[A-Za-z0-9]+$' AND scope_id <> 'org')
    OR (scope = 'org'  AND scope_id = 'org')
  );

-- #37 — eval_runs: persistent record of scheduled regression eval results.
-- A pg-boss cron in fp-worker (eval-worker.ts) runs a fixed set of deterministic
-- probes against the chemistry plumbing (DB reachability, FTS, query helpers,
-- rate-limit math) and writes one row per run. Week-over-week comparison comes
-- from the `started_at` timestamp + `fixtures_passed` ratio; per-fixture detail
-- lives in `scores` JSONB so a new probe doesn't need a schema change.
--
-- LLM-level scoring (calling the agent SDK on each fixture) is deferred — the
-- BACKLOG note "deferred from v2.1 to keep scope tight" still applies. The
-- deterministic probes catch the cheap regressions (tool rot, missing indexes,
-- broken MCP, query-helper crashes) without per-run model cost.
CREATE TABLE IF NOT EXISTS eval_runs (
  id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  started_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  finished_at       TIMESTAMPTZ,
  fixtures_total    INTEGER NOT NULL,
  fixtures_passed   INTEGER NOT NULL,
  scores            JSONB NOT NULL DEFAULT '[]'::jsonb,
  notes             TEXT
);

CREATE INDEX IF NOT EXISTS eval_runs_started_at_idx
  ON eval_runs (started_at DESC);

ALTER TABLE eval_runs ENABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS eval_runs_all ON eval_runs;
-- BACKLOG #49: ships permissive per v1 single-tenant convention; tighten
-- once tenants > 1 alongside the other stub policies listed there.
CREATE POLICY eval_runs_all ON eval_runs FOR ALL USING (true) WITH CHECK (true);
