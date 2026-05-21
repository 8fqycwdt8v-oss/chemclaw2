-- Security fix: drop permissive RLS stubs across the remaining 22 tables.
--
-- Migration 0034 disabled RLS + dropped the `USING (true)` stub policies on
-- agent_sessions and rate_limits. This is the follow-up for every other
-- table that landed RLS the same way: ENABLE ROW LEVEL SECURITY + a
-- permissive policy that filters nothing.
--
-- CLAUDE.md §security-2: don't enable RLS without per-tenant predicates.
-- `USING (true)` is footgun, not policy — it looks protected to a casual
-- `\d+` but provides zero isolation. Worse, if a future migration switches
-- the app to a least-priv role expecting RLS to filter, every row stays
-- wide open. Until per-tenant predicates exist (which require the app to
-- SET LOCAL app.current_user_id on every transaction — never wired up,
-- see 0021 / 0034 comments), RLS being OFF makes the actual state visible
-- to anyone reading the schema.
--
-- App-layer authz (Clerk + owner-scoped queries) remains the access control
-- mechanism for every table touched here. The follow-up to add real
-- per-tenant predicates is BACKLOG.md Tier F "Multi-tenant RLS", triggered
-- when tenant count > 1.

-- 0005_audit.sql
ALTER TABLE compounds DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS compounds_allow ON compounds;

ALTER TABLE reactions DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS reactions_allow ON reactions;

ALTER TABLE wiki_pages DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS wiki_pages_allow ON wiki_pages;

ALTER TABLE synthesis_campaigns DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS synthesis_campaigns_allow ON synthesis_campaigns;

ALTER TABLE campaign_steps DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS campaign_steps_allow ON campaign_steps;

ALTER TABLE wiki_chunks DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS wiki_chunks_allow ON wiki_chunks;

ALTER TABLE wiki_citations DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS wiki_citations_allow ON wiki_citations;

ALTER TABLE audit_log DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS audit_log_allow ON audit_log;
-- 0006_schema_hardening.sql added a second policy on audit_log:
DROP POLICY IF EXISTS audit_log_select ON audit_log;

-- 0012_wiki_revisions.sql
ALTER TABLE wiki_revisions DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS wiki_revisions_select ON wiki_revisions;

-- 0014_campaign_approval_and_subscriptions.sql
ALTER TABLE wiki_subscriptions DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS wiki_subscriptions_all ON wiki_subscriptions;

-- 0016_feedback_and_overrides.sql
ALTER TABLE agent_feedback DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agent_feedback_all ON agent_feedback;

ALTER TABLE agent_overrides DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agent_overrides_select ON agent_overrides;

-- 0018_wiki_contradictions.sql
ALTER TABLE wiki_contradictions DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS wiki_contradictions_select ON wiki_contradictions;

-- 0019_tool_permissions.sql
ALTER TABLE tool_permissions DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS tool_permissions_all ON tool_permissions;

-- 0023_budget_caps.sql
ALTER TABLE project_budgets DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS project_budgets_all ON project_budgets;

ALTER TABLE project_budget_spend DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS project_budget_spend_all ON project_budget_spend;

-- 0024_agent_todos.sql
ALTER TABLE agent_todos DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS agent_todos_all ON agent_todos;

-- 0026_knowledge_persistence.sql
ALTER TABLE external_facts DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS external_facts_all ON external_facts;

ALTER TABLE properties DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS properties_all ON properties;

ALTER TABLE papers DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS papers_all ON papers;

-- 0027_wiki_tables_and_token_budgets.sql
ALTER TABLE wiki_tables DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS wiki_tables_all ON wiki_tables;

-- 0028_wiki_proposed_edits.sql
ALTER TABLE wiki_proposed_edits DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS wiki_proposed_edits_all ON wiki_proposed_edits;

-- 0029_tool_perm_check_and_eval_runs.sql
ALTER TABLE eval_runs DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS eval_runs_all ON eval_runs;

-- 0033_users.sql
ALTER TABLE users DISABLE ROW LEVEL SECURITY;
DROP POLICY IF EXISTS users_all ON users;
