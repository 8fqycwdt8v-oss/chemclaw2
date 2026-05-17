-- Wiki page lifecycle flags: needs_review (flagged for human attention),
-- archived (hidden from default list), maturity tier (exploratory → validated → authoritative).
ALTER TABLE wiki_pages
  ADD COLUMN IF NOT EXISTS needs_review BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS archived BOOLEAN NOT NULL DEFAULT false,
  ADD COLUMN IF NOT EXISTS maturity TEXT NOT NULL DEFAULT 'exploratory'
    CHECK (maturity IN ('exploratory', 'validated', 'authoritative')),
  ADD COLUMN IF NOT EXISTS project TEXT NULL;

CREATE INDEX IF NOT EXISTS wiki_pages_project_idx ON wiki_pages (project) WHERE project IS NOT NULL;

-- Citation-level disputed flag — let experts mark a literature claim as contested
-- without deleting it. Renders strikethrough in UI; the citation remains for trace.
ALTER TABLE wiki_citations
  ADD COLUMN IF NOT EXISTS disputed BOOLEAN NOT NULL DEFAULT false;

-- Bi-temporal columns on entity tables (commitment from chemclaw2_features.md §5.1).
-- valid_from is system-set on insert; valid_to is NULL while the row is current.
-- App-side reads stay version-agnostic for v1.6; pointInTimeWiki uses wiki_revisions.
ALTER TABLE compounds   ADD COLUMN IF NOT EXISTS valid_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                         ADD COLUMN IF NOT EXISTS valid_to   TIMESTAMPTZ NULL;
ALTER TABLE reactions   ADD COLUMN IF NOT EXISTS valid_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                         ADD COLUMN IF NOT EXISTS valid_to   TIMESTAMPTZ NULL;
ALTER TABLE wiki_pages  ADD COLUMN IF NOT EXISTS valid_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                         ADD COLUMN IF NOT EXISTS valid_to   TIMESTAMPTZ NULL;
