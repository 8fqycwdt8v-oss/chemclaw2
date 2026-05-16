-- v2.1-D1: project budget caps (chemclaw2_features.md §3.5).
--
-- project_budgets stores the policy per project_key. project_key matches the
-- agent's scoped session key (today: "chemclaw2:<userId>") — a real "projects"
-- table can replace this once there is more than one tenant.
--
-- project_budget_spend stores the rolling counter per period start. The Pre-LLM
-- and PostToolUse hooks read+increment this table; one row per (project_key,
-- period_start) means a daily/weekly/monthly rollover is a fresh row, not an
-- UPDATE — old rows stay as a history trail.
--
-- Tracked metrics:
--   - tool_calls: incremented in PostToolUse for every tool invocation
--   - experiments: incremented when kickoff_campaign is called (a real
--     experiment dispatch). Tokens / cost tracking is deferred — needs a wrap
--     of the SDK stream, not just a tool hook.

CREATE TABLE IF NOT EXISTS project_budgets (
  project_key       TEXT PRIMARY KEY,
  period            TEXT NOT NULL CHECK (period IN ('day', 'week', 'month')),
  tool_calls_cap    BIGINT,
  experiments_cap   INTEGER,
  updated_by        TEXT NOT NULL,
  updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS project_budget_spend (
  project_key   TEXT NOT NULL,
  period_start  TIMESTAMPTZ NOT NULL,
  tool_calls    BIGINT NOT NULL DEFAULT 0,
  experiments   INTEGER NOT NULL DEFAULT 0,
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (project_key, period_start)
);

CREATE INDEX IF NOT EXISTS project_budget_spend_recent_idx
  ON project_budget_spend (project_key, period_start DESC);

-- RLS stubs match the existing convention (per-tenant predicates deferred
-- until multi-tenant — see BACKLOG L4).
ALTER TABLE project_budgets ENABLE ROW LEVEL SECURITY;
CREATE POLICY project_budgets_all ON project_budgets FOR ALL USING (true) WITH CHECK (true);

ALTER TABLE project_budget_spend ENABLE ROW LEVEL SECURITY;
CREATE POLICY project_budget_spend_all ON project_budget_spend FOR ALL USING (true) WITH CHECK (true);
