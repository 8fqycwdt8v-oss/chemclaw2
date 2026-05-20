-- HNSW index on paper_chunks.embedding for sub-100ms semantic search.
--
-- Single-statement file. CONCURRENTLY means this can't run inside a
-- transaction — the CI apply loop detects "CONCURRENTLY" and uses
-- autocommit instead of --single-transaction for this file (see
-- .github/workflows/ci.yml and migrations/MIGRATIONS.md).
--
-- vector_cosine_ops matches the `<=>` cosine-distance operator used
-- by api/db/queries/papers.py:semantic_search_paper_chunks. Default
-- HNSW params (m=16, ef_construction=64) — tune only when query
-- latency or recall is measured as the bottleneck.
CREATE INDEX CONCURRENTLY IF NOT EXISTS paper_chunks_embedding_hnsw_idx
    ON paper_chunks USING hnsw (embedding vector_cosine_ops);
