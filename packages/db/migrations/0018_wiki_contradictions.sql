-- H4: store the agent's auto-proposed resolution of contradicting citations.
-- Each row captures the two citations in dispute, the proposed winner, and
-- the reasoning. Append-only — a project lead reads the resolution and either
-- accepts it (via setCitationDisputed on the losing side) or files a fresh
-- contradiction row with a different verdict.
CREATE TABLE IF NOT EXISTS wiki_contradictions (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  page_id             UUID NOT NULL REFERENCES wiki_pages(id) ON DELETE CASCADE,
  citation_a          TEXT NOT NULL,
  citation_b          TEXT NOT NULL,
  proposed_winner     TEXT NOT NULL CHECK (proposed_winner IN ('a', 'b', 'inconclusive')),
  reason              TEXT NOT NULL,
  resolved_by         TEXT,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS wiki_contradictions_page_idx ON wiki_contradictions (page_id, created_at DESC);

ALTER TABLE wiki_contradictions ENABLE ROW LEVEL SECURITY;
CREATE POLICY wiki_contradictions_select ON wiki_contradictions FOR SELECT USING (true);
CREATE POLICY wiki_contradictions_insert ON wiki_contradictions FOR INSERT WITH CHECK (true);
