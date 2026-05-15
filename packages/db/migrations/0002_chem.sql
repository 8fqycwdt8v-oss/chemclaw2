CREATE TABLE compounds (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  smiles      TEXT NOT NULL,
  canon_smiles TEXT,
  name        TEXT,
  cas_number  TEXT,
  morgan_fp   BIT(2048),
  fp_computed_at TIMESTAMPTZ,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_by  TEXT NOT NULL
);

CREATE TABLE reactions (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  rxn_smiles  TEXT NOT NULL,
  name        TEXT,
  conditions  TEXT,
  drfp        BIT(2048),
  fp_computed_at TIMESTAMPTZ,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_by  TEXT NOT NULL
);

-- HNSW indexes on bit(2048) using Hamming distance (pgvector 0.7+)
CREATE INDEX compounds_morgan_fp_hnsw ON compounds
  USING hnsw (morgan_fp bit_hamming_ops) WITH (m = 16, ef_construction = 64);

CREATE INDEX reactions_drfp_hnsw ON reactions
  USING hnsw (drfp bit_hamming_ops) WITH (m = 16, ef_construction = 64);

-- Fingerprint jobs are enqueued by the fp-worker process, which polls for rows
-- with NULL fingerprints every 30 seconds using pg-boss.send() (TypeScript API).
-- No SQL trigger is used because pg-boss v10 has no pgboss.send() SQL function.
