-- Add UNIQUE constraint on (page_id, citation_id, source_type, source_id) so
-- concurrent upsertWikiPage + setCitationDisputed calls can't create duplicate
-- citation rows. upsertWikiPage already DELETEs-then-INSERTs in one transaction,
-- so duplicates don't occur via that path; this guards future writers.
-- COALESCE-on-source_id because NULL distinctness in PostgreSQL otherwise allows
-- duplicates where source_id is NULL.
CREATE UNIQUE INDEX IF NOT EXISTS wiki_citations_unique_idx
  ON wiki_citations (page_id, citation_id, source_type, COALESCE(source_id, ''));
