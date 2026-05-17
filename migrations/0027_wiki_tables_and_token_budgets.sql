-- Wave-2c: tabular extraction + LLM-token budget tracking.
--
-- Two independent additions bundled in one migration because both are small
-- schema deltas and changing the data model twice in one wave is more review
-- pain than one combined migration.
--
-- 1. wiki_tables — markdown tables in wiki bodies get extracted to rows here
--    so SAR-style queries ("yield vs catalyst across these conditions") run
--    in SQL instead of agent-side text parsing. Header embedding lets the
--    semantic-search path surface "tables about yields" without scanning
--    every chunk.
-- 2. project_budgets.tokens_cap + project_budget_spend.tokens — Wave 2a's
--    note in BACKLOG flagged that token spend wasn't tracked. The chat
--    route now extracts SDK usage at end-of-stream and increments here.

CREATE TABLE IF NOT EXISTS wiki_tables (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  page_id          UUID NOT NULL REFERENCES wiki_pages(id) ON DELETE CASCADE,
  position         INTEGER NOT NULL,    -- 0-indexed order within the page body
  anchor           TEXT,                -- surrounding heading text for navigation
  headers          JSONB NOT NULL,      -- array of header strings
  rows             JSONB NOT NULL,      -- array of objects keyed by header
  header_text      TEXT NOT NULL,       -- denormalized header join for cheap FTS
  header_embedding vector(1536),        -- nullable; populated when an embedFn is available
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS wiki_tables_page_idx ON wiki_tables (page_id, position);
CREATE INDEX IF NOT EXISTS wiki_tables_header_fts_idx
  ON wiki_tables USING gin (to_tsvector('english', header_text));
-- HNSW index gated on the embedding column being non-null; deferred until row
-- counts justify the build cost.
ALTER TABLE wiki_tables ENABLE ROW LEVEL SECURITY;
CREATE POLICY wiki_tables_all ON wiki_tables FOR ALL USING (true) WITH CHECK (true);


ALTER TABLE project_budgets
  ADD COLUMN IF NOT EXISTS tokens_cap BIGINT;

ALTER TABLE project_budget_spend
  ADD COLUMN IF NOT EXISTS tokens BIGINT NOT NULL DEFAULT 0;
