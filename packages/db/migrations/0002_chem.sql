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

-- pg-boss job enqueue function: called by triggers on INSERT
CREATE OR REPLACE FUNCTION enqueue_fp_job() RETURNS TRIGGER AS $$
BEGIN
  IF TG_TABLE_NAME = 'compounds' THEN
    PERFORM pgboss.send('compute-morgan-fp', jsonb_build_object('id', NEW.id));
  ELSIF TG_TABLE_NAME = 'reactions' THEN
    PERFORM pgboss.send('compute-drfp', jsonb_build_object('id', NEW.id));
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER compounds_enqueue_fp
  AFTER INSERT ON compounds
  FOR EACH ROW WHEN (NEW.morgan_fp IS NULL)
  EXECUTE FUNCTION enqueue_fp_job();

CREATE TRIGGER reactions_enqueue_fp
  AFTER INSERT ON reactions
  FOR EACH ROW WHEN (NEW.drfp IS NULL)
  EXECUTE FUNCTION enqueue_fp_job();
