-- Security fix: drop permissive RLS stubs on agent_sessions and rate_limits.
--
-- Migration 0021 enabled RLS with `USING (true) WITH CHECK (true)` on these
-- two tables (and others). That's a false sense of security: it looks
-- protected to a casual `\d+` but provides zero isolation. Worse, if a future
-- migration switches the app to a least-priv role expecting RLS to filter,
-- transcripts and rate-limit keys stay wide open.
--
-- Until per-tenant predicates exist (which require the app to SET LOCAL
-- app.current_user_id on every transaction — withUserContext was exported
-- but never invoked, per the 0021 comment), RLS being OFF makes the actual
-- state visible to anyone reading the schema.
--
-- agent_sessions holds full LLM transcripts; rate_limits keys include
-- user_id. App-layer authz (Clerk + requireAdminApi) remains the access
-- control mechanism for both.

ALTER TABLE agent_sessions DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agent_sessions_all ON agent_sessions;

ALTER TABLE rate_limits DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS rate_limits_all ON rate_limits;
