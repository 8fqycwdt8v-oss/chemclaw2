-- wiki_citations.page_id is used in DELETE WHERE page_id = $1 on every upsertWikiPage call
-- but had no index, causing sequential scans. Add it here.
CREATE INDEX IF NOT EXISTS wiki_citations_page_id_idx ON wiki_citations (page_id);
