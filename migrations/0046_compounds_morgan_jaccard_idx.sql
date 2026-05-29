-- Jaccard-distance HNSW index on compounds.morgan_fp.
--
-- bit_jaccard_ops backs the `<%>` operator, where Jaccard distance =
-- 1 - Tanimoto. This is the field-standard similarity metric for binary
-- chemical fingerprints (Morgan/ECFP4), and it matches the Tanimoto
-- rerank/threshold applied in api/db/queries/compounds.py. Aligning the
-- ANN pruning metric with the final ranking metric is what makes the
-- candidate pool returned by ORDER BY ... `<%>` the true top-Tanimoto
-- neighbours. Replaces the bit_hamming_ops index dropped in 0045.
--
-- Single-statement file. CREATE INDEX CONCURRENTLY cannot run inside a
-- transaction; the CI apply loop detects "CONCURRENTLY" and uses
-- autocommit (see migrations/MIGRATIONS.md). Default HNSW params
-- (m=16, ef_construction=64) mirror the original 0002 index.
CREATE INDEX CONCURRENTLY IF NOT EXISTS compounds_morgan_fp_jaccard_hnsw
    ON compounds USING hnsw (morgan_fp bit_jaccard_ops)
    WITH (m = 16, ef_construction = 64);
