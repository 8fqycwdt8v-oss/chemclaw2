-- pg-boss stores queue configuration in pgboss.queue, which is created when the
-- application first calls boss.start(). On a fresh database (e.g. CI) that table
-- does not exist yet, so we guard with a conditional block.
-- For existing deployments where run-campaign-step was created with standard policy,
-- this migration updates it to stately so singletonKey deduplication takes effect.
-- For fresh deployments, createQueue() in application code will create it with stately.
DO $$
BEGIN
  IF EXISTS (
    SELECT 1 FROM information_schema.tables
    WHERE table_schema = 'pgboss' AND table_name = 'queue'
  ) THEN
    UPDATE pgboss.queue SET policy = 'stately' WHERE name = 'run-campaign-step';
  END IF;
END $$;
