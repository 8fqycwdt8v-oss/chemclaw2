-- wiki_revisions: immutable history of wiki_pages content.
-- The BEFORE UPDATE trigger on wiki_pages (migration 0003) increments version;
-- this AFTER UPDATE trigger snapshots the OLD row into wiki_revisions, so the
-- prior content survives the next overwrite. Reads use listWikiRevisions.
CREATE TABLE IF NOT EXISTS wiki_revisions (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  page_id      UUID NOT NULL REFERENCES wiki_pages(id) ON DELETE CASCADE,
  version      INTEGER NOT NULL,
  title        TEXT NOT NULL,
  content      JSONB NOT NULL,
  content_text TEXT,
  updated_by   TEXT,
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (page_id, version)
);

CREATE INDEX IF NOT EXISTS wiki_revisions_page_version_idx
  ON wiki_revisions (page_id, version DESC);

CREATE OR REPLACE FUNCTION snapshot_wiki_revision() RETURNS TRIGGER AS $$
BEGIN
  -- Only snapshot when the content or title actually changed
  IF NEW.content::text IS DISTINCT FROM OLD.content::text
     OR NEW.content_text IS DISTINCT FROM OLD.content_text
     OR NEW.title IS DISTINCT FROM OLD.title THEN
    INSERT INTO wiki_revisions (page_id, version, title, content, content_text, updated_by, updated_at)
    VALUES (OLD.id, OLD.version, OLD.title, OLD.content, OLD.content_text, OLD.updated_by, OLD.updated_at);
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS wiki_pages_snapshot_revision ON wiki_pages;
CREATE TRIGGER wiki_pages_snapshot_revision
  AFTER UPDATE ON wiki_pages
  FOR EACH ROW EXECUTE FUNCTION snapshot_wiki_revision();

-- RLS append-only: select for inspection, insert for the trigger, no update/delete.
ALTER TABLE wiki_revisions ENABLE ROW LEVEL SECURITY;
CREATE POLICY wiki_revisions_select ON wiki_revisions FOR SELECT USING (true);
CREATE POLICY wiki_revisions_insert ON wiki_revisions FOR INSERT WITH CHECK (true);
