-- wiki_chunks: remove duplicate (page_id, chunk_idx) rows before adding UNIQUE
-- constraint. Keep the row with the smallest UUID value when duplicates exist.
-- Note: UUIDs are random — smallest UUID is not necessarily the oldest row.
DELETE FROM wiki_chunks a
  USING wiki_chunks b
  WHERE a.page_id = b.page_id
    AND a.chunk_idx = b.chunk_idx
    AND a.id > b.id;

-- Now safe to add the uniqueness constraint
ALTER TABLE wiki_chunks
  ADD CONSTRAINT wiki_chunks_page_chunk_unique UNIQUE (page_id, chunk_idx);

-- agent_sessions: add insert_seq bigserial for deterministic replay ordering.
-- mtime (Date.now()) can tie across concurrent appends in the same millisecond;
-- bigserial is strictly monotone per-insert, giving correct session replay order.
-- Pre-existing rows receive insert_seq values assigned by bigserial backfill order
-- (not guaranteed to match original insertion order); replay of historical sessions
-- should use ORDER BY insert_seq ASC, mtime ASC as a tiebreaker.
ALTER TABLE agent_sessions
  ADD COLUMN insert_seq BIGSERIAL;

CREATE INDEX IF NOT EXISTS agent_sessions_insert_seq_idx
  ON agent_sessions (project_key, session_id, insert_seq);

-- audit_log RLS: replace permissive FOR ALL policy with per-operation policies
-- that allow SELECT and INSERT but block UPDATE and DELETE.
-- This enforces append-only semantics; no policy for an operation = deny under RLS.
DROP POLICY IF EXISTS audit_log_allow ON audit_log;
CREATE POLICY audit_log_select ON audit_log FOR SELECT USING (true);
CREATE POLICY audit_log_insert ON audit_log FOR INSERT WITH CHECK (true);
