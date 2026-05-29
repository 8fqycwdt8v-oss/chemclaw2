-- Jaccard-distance HNSW index on reactions.drfp.
--
-- bit_jaccard_ops backs the `<%>` operator (Jaccard distance =
-- 1 - Tanimoto), matching the Tanimoto rerank in find_similar_reactions
-- (api/db/queries/reactions.py). Replaces the bit_hamming_ops index
-- dropped in 0047.
--
-- Single-statement file; see migrations/MIGRATIONS.md for the
-- CONCURRENTLY/autocommit handling. Default HNSW params (m=16,
-- ef_construction=64) mirror the original 0002 index.
CREATE INDEX CONCURRENTLY IF NOT EXISTS reactions_drfp_jaccard_hnsw
    ON reactions USING hnsw (drfp bit_jaccard_ops)
    WITH (m = 16, ef_construction = 64);
