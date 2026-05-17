-- F1 agent_feedback: one row per (session, turn) when the user clicks 👍 or 👎.
-- Score is -1 or +1; reason is optional free text. Append-only; users can submit
-- one feedback per (session, turn) — re-submitting overwrites via the unique
-- constraint + ON CONFLICT in the route.
CREATE TABLE IF NOT EXISTS agent_feedback (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id    TEXT      NOT NULL,
  turn_index    INTEGER   NOT NULL,
  score         INTEGER   NOT NULL CHECK (score IN (-1, 1)),
  reason        TEXT,
  user_id       TEXT      NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (session_id, turn_index, user_id)
);
CREATE INDEX IF NOT EXISTS agent_feedback_session_idx ON agent_feedback (session_id, turn_index);
CREATE INDEX IF NOT EXISTS agent_feedback_user_idx ON agent_feedback (user_id, created_at DESC);

-- F5 agent_overrides: when a chemist supplies a justification to bypass the
-- scheduled-substance gate, the request + justification is recorded BEFORE the
-- agent runs. Append-only — never updated.
CREATE TABLE IF NOT EXISTS agent_overrides (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id    TEXT      NOT NULL,
  user_id       TEXT      NOT NULL,
  gate_name     TEXT      NOT NULL,
  justification TEXT      NOT NULL,
  prompt_hash   TEXT      NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS agent_overrides_user_idx ON agent_overrides (user_id, created_at DESC);

-- RLS: app-level filtering does scoping today; these policies are stub-permissive
-- like the rest of the tables (see migration 0006). Append-only on agent_overrides
-- via no UPDATE/DELETE policies (default-deny under RLS).
ALTER TABLE agent_feedback ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_feedback_all ON agent_feedback FOR ALL USING (true) WITH CHECK (true);

ALTER TABLE agent_overrides ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_overrides_select ON agent_overrides FOR SELECT USING (true);
CREATE POLICY agent_overrides_insert ON agent_overrides FOR INSERT WITH CHECK (true);
