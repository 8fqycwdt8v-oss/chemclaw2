-- Partial index for the dead-letter sweep query in campaign-worker.ts, which filters
-- on status='running' with retry_count < 3 and updated_at older than 30 minutes.
-- Without this, every 5-minute cron sweep performs a sequential scan on campaign_steps.
CREATE INDEX IF NOT EXISTS campaign_steps_dead_letter_idx ON campaign_steps (updated_at)
  WHERE status = 'running' AND retry_count < 3;
