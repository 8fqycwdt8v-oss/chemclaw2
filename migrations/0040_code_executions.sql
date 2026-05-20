-- Phase C: persistent log of agent-authored Python executions.
--
-- The code sandbox MCP (`packages/mcp-servers/mcp_codesandbox`) runs each
-- snippet as a separate subprocess with strict resource limits. Every
-- invocation is logged here so investigations have a full audit trail of
-- what the agent computed, with what code, against what stdout/stderr
-- and exit status. Tied to investigation_id (Phase B) so a research
-- thread can replay its own analysis history.
--
-- This is the chemclaw2 analogue of Kosmos's data-analysis agent rollout
-- log: the agent's reasoning links back to a specific code artifact, not
-- just a freeform answer.

CREATE TABLE IF NOT EXISTS code_executions (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  investigation_id UUID REFERENCES investigations(id) ON DELETE CASCADE,
  session_id       TEXT,                                      -- nullable; allows ad-hoc runs outside an investigation
  code             TEXT NOT NULL,
  language         TEXT NOT NULL DEFAULT 'python'
                   CHECK (language IN ('python')),
  stdout           TEXT NOT NULL DEFAULT '',
  stderr           TEXT NOT NULL DEFAULT '',
  exit_code        INTEGER NOT NULL,                          -- 0 = success; 124 = our timeout sentinel
  duration_ms      INTEGER NOT NULL DEFAULT 0,
  status           TEXT NOT NULL DEFAULT 'completed'
                   CHECK (status IN ('completed', 'timeout', 'error', 'killed')),
  created_by       TEXT NOT NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK (investigation_id IS NOT NULL OR session_id IS NOT NULL)
);

CREATE INDEX IF NOT EXISTS code_executions_investigation_idx
    ON code_executions (investigation_id, created_at DESC)
    WHERE investigation_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS code_executions_owner_idx
    ON code_executions (created_by, created_at DESC);
