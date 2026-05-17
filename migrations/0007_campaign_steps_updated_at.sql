-- Add updated_at to campaign_steps for dead-letter sweep detection.
-- Steps stuck in 'running' for > 30 minutes are assumed crashed and reset by the worker.
ALTER TABLE campaign_steps
  ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW();

-- Trigger to keep updated_at current on every row update
CREATE OR REPLACE FUNCTION update_campaign_steps_updated_at() RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER campaign_steps_auto_updated_at
  BEFORE UPDATE ON campaign_steps
  FOR EACH ROW EXECUTE FUNCTION update_campaign_steps_updated_at();
