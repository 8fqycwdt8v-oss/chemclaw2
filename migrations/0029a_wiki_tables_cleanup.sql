-- Wave-3f cut: drop write-only-null infrastructure on wiki_tables.
--
-- 0027 added a header_embedding vector(1536) column on wiki_tables intended
-- for future semantic retrieval of "tables about yields"-style queries. The
-- live wiki_upsert path hard-codes `headerEmbedding: null`; no caller passes
-- `embedHeader` to `upsertTablesForPage`; no tool surfaces
-- searchTablesByHeader. Speculative infrastructure per CLAUDE.md "Defer
-- until measured" — drop it. The schema can be re-added when an actual
-- "find tables about X" use case lands.
--
-- header_text stays (it's the GIN-FTS target for searchTablesByHeader, but
-- since we're also dropping searchTablesByHeader and that index, the column
-- becomes dead too). Drop both column + the GIN index in one migration.
DROP INDEX IF EXISTS wiki_tables_header_fts_idx;
ALTER TABLE wiki_tables DROP COLUMN IF EXISTS header_embedding;
ALTER TABLE wiki_tables DROP COLUMN IF EXISTS header_text;
