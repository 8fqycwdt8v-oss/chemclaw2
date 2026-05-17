-- Drop campaign_steps_status_retry_idx (added in 0031). It's redundant with
-- the older campaign_steps_retry_idx from migration 0004:
--   0004: (next_retry_at) WHERE status='failed' AND retry_count < 3
--   0031: (status, next_retry_at) WHERE status='failed'
-- The 0004 partial is strictly more selective for the actual getStepsForRetry
-- query (which adds retry_count < 3) and has a smaller key. The 0031 index
-- never wins over it.
DROP INDEX IF EXISTS campaign_steps_status_retry_idx;
