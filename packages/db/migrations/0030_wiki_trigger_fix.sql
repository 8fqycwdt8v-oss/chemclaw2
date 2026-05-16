-- Fix R1: PR #35's update_wiki_updated_at over-gated. It pinned BOTH version
-- AND updated_at to OLD on metadata-only writes, which means archived /
-- needs_review / project / maturity changes no longer reorder the wiki list
-- view and the "updated DATE" display stays stale. The intent was only to
-- avoid bumping `version` (which drives subscriber unread notifications).
-- Always advance updated_at; only bump version on real content changes.
CREATE OR REPLACE FUNCTION update_wiki_updated_at() RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  IF NEW.content::text IS DISTINCT FROM OLD.content::text
     OR NEW.content_text IS DISTINCT FROM OLD.content_text
     OR NEW.title IS DISTINCT FROM OLD.title THEN
    NEW.version = OLD.version + 1;
  ELSE
    NEW.version = OLD.version;
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;
