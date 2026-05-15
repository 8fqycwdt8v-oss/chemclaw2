CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

CREATE TABLE IF NOT EXISTS agent_sessions (
  project_key TEXT NOT NULL,
  session_id  TEXT NOT NULL,
  subpath     TEXT NOT NULL DEFAULT '',
  entries     JSONB NOT NULL DEFAULT '[]',
  mtime       BIGINT NOT NULL,
  PRIMARY KEY (project_key, session_id, subpath)
);

CREATE INDEX IF NOT EXISTS agent_sessions_mtime_idx
  ON agent_sessions (project_key, mtime DESC);
