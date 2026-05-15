CREATE TABLE synthesis_campaigns (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id   TEXT NOT NULL,
  target_smiles TEXT,
  status        TEXT NOT NULL DEFAULT 'planning',
  plan          JSONB,
  wiki_page_id  UUID REFERENCES wiki_pages(id),
  created_by    TEXT NOT NULL,
  created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE campaign_steps (
  id            UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  campaign_id   UUID NOT NULL REFERENCES synthesis_campaigns(id) ON DELETE CASCADE,
  step_idx      INTEGER NOT NULL,
  reaction_smiles TEXT,
  conditions    TEXT,
  status        TEXT NOT NULL DEFAULT 'pending',
  result        JSONB,
  retry_count   INTEGER NOT NULL DEFAULT 0,
  next_retry_at TIMESTAMPTZ
);

CREATE INDEX campaign_steps_retry_idx ON campaign_steps (next_retry_at)
  WHERE status = 'failed' AND retry_count < 3;

CREATE FUNCTION update_campaign_updated_at() RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER synthesis_campaigns_auto_updated_at
  BEFORE UPDATE ON synthesis_campaigns
  FOR EACH ROW EXECUTE FUNCTION update_campaign_updated_at();
