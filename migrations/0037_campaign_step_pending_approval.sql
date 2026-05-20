-- Add 'pending_approval' to campaign_steps.status — a step the agent
-- proposes but won't execute until the user explicitly approves via
-- POST /api/campaigns/{cid}/steps/{idx}/approve.
--
-- The worker's hot path (`get_pending_steps_for_campaigns`) filters on
-- `status = 'pending'`, so steps in 'pending_approval' are silently
-- skipped until promoted to 'pending'. Spec §3.11 ("intervene mid-flight
-- or resume paused").

ALTER TABLE campaign_steps
  DROP CONSTRAINT IF EXISTS campaign_steps_status_chk;

ALTER TABLE campaign_steps
  ADD CONSTRAINT campaign_steps_status_chk
  CHECK (status IN ('pending', 'pending_approval', 'running', 'complete', 'failed'));
