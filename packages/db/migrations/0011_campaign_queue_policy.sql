-- pg-boss stores queue configuration in pgboss.queue. createQueue() in application code
-- is idempotent (ON CONFLICT DO NOTHING), so changing the policy in TypeScript has no
-- effect on an already-existing queue. Update the row directly so the stately policy
-- is enforced for the run-campaign-step queue in all environments.
UPDATE pgboss.queue SET policy = 'stately' WHERE name = 'run-campaign-step';
