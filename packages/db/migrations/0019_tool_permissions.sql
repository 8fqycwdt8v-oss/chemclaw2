-- J2: per-tool authorization. Admins set deny/ask/allow rules scoped per user,
-- project, or org-wide. The agent's canUseTool callback (apps/web/lib/agent.ts)
-- reads this table on each tool invocation. Lookup precedence: user → project →
-- org → default (allow).
CREATE TABLE IF NOT EXISTS tool_permissions (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  scope       TEXT NOT NULL CHECK (scope IN ('user', 'project', 'org')),
  scope_id    TEXT NOT NULL,
  tool_name   TEXT NOT NULL,
  mode        TEXT NOT NULL CHECK (mode IN ('allow', 'ask', 'deny')),
  updated_by  TEXT NOT NULL,
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (scope, scope_id, tool_name)
);
CREATE INDEX IF NOT EXISTS tool_permissions_lookup_idx
  ON tool_permissions (scope_id, tool_name);

ALTER TABLE tool_permissions ENABLE ROW LEVEL SECURITY;
CREATE POLICY tool_permissions_all ON tool_permissions FOR ALL USING (true) WITH CHECK (true);
