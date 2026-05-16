-- v2.1-B2: per-session agent todo list. begin_deep_research writes the
-- generated checklist into this table; finalize_deep_research marks the
-- session's todos done. The chat UI reads /api/session/<id>/todos to render
-- the running todo list alongside the conversation.
--
-- The trigger from 0022 cascades deletions when the underlying agent_sessions
-- row is removed (same shape as agent_feedback / agent_overrides).
CREATE TABLE IF NOT EXISTS agent_todos (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id   TEXT NOT NULL,
  user_id      TEXT NOT NULL,
  text         TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'done')),
  position     INTEGER NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS agent_todos_session_idx
  ON agent_todos (session_id, position);

ALTER TABLE agent_todos ENABLE ROW LEVEL SECURITY;
CREATE POLICY agent_todos_all ON agent_todos FOR ALL USING (true) WITH CHECK (true);

-- Extend the session cascade trigger from 0022 to also remove todos.
CREATE OR REPLACE FUNCTION cascade_session_audit_rows() RETURNS TRIGGER AS $$
BEGIN
  DELETE FROM agent_feedback WHERE session_id = OLD.session_id;
  DELETE FROM agent_overrides WHERE session_id = OLD.session_id;
  DELETE FROM agent_todos WHERE session_id = OLD.session_id;
  RETURN OLD;
END;
$$ LANGUAGE plpgsql;
