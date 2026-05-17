-- C1: Gate wiki_pages_auto_version trigger on real content changes.
-- Previously, every UPDATE (including metadata-only PATCHes for archived /
-- needs_review / project / maturity) bumped version and triggered "unread"
-- notifications for subscribers. Mirror the predicate already used by
-- snapshot_wiki_revision (migration 0012).
CREATE OR REPLACE FUNCTION update_wiki_updated_at() RETURNS TRIGGER AS $$
BEGIN
  IF NEW.content::text IS DISTINCT FROM OLD.content::text
     OR NEW.content_text IS DISTINCT FROM OLD.content_text
     OR NEW.title IS DISTINCT FROM OLD.title THEN
    NEW.updated_at = NOW();
    NEW.version = OLD.version + 1;
  ELSE
    NEW.updated_at = OLD.updated_at;
    NEW.version = OLD.version;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

-- H8: content_text is the source of truth for FTS + chunking; make NOT NULL.
-- Backfill any historical NULLs first so the constraint doesn't fail.
UPDATE wiki_pages SET content_text = '' WHERE content_text IS NULL;
ALTER TABLE wiki_pages ALTER COLUMN content_text SET DEFAULT '';
ALTER TABLE wiki_pages ALTER COLUMN content_text SET NOT NULL;

-- L1: updated_by should always be set on writes (audit integrity).
-- Backfill any historical NULLs with created_by.
UPDATE wiki_pages SET updated_by = created_by WHERE updated_by IS NULL;
ALTER TABLE wiki_pages ALTER COLUMN updated_by SET NOT NULL;

-- M9: Reverse-lookup index for "which pages cite this source?" — common
-- workflow when a paper is retracted or a DOI is replaced.
CREATE INDEX IF NOT EXISTS wiki_citations_source_idx
  ON wiki_citations (source_type, source_id) WHERE source_id IS NOT NULL;

-- L7: Composite index for listWikiPages keyset pagination over
-- (updated_at DESC, id DESC).
CREATE INDEX IF NOT EXISTS wiki_pages_updated_at_id_idx
  ON wiki_pages (updated_at DESC, id DESC);
