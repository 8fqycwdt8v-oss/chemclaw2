-- Reaction outcomes: experimental results attached to a reaction (or a
-- campaign step). Feeds the process-gap-analyst sub-agent so it can see
-- not just "what was tried" but "how it went" when proposing follow-up
-- questions for a reaction step.
--
-- Sources:
--   eln       — pulled from the connected ELN via ingest_eln_experiment
--   manual    — recorded by the agent from a user-pasted summary
--   campaign  — derived from campaign_steps.result (future backfill)
--
-- Idempotency:
--   eln_experiment_id is unique (partial index, NULLs not enforced) so a
--   second ingest of the same ELN record updates / no-ops cleanly.

CREATE TABLE IF NOT EXISTS reaction_outcomes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reaction_id UUID NOT NULL REFERENCES reactions(id) ON DELETE CASCADE,
    campaign_step_id UUID NULL REFERENCES campaign_steps(id) ON DELETE SET NULL,
    eln_experiment_id TEXT NULL,
    source TEXT NOT NULL CHECK (source IN ('eln', 'manual', 'campaign')),
    status TEXT NOT NULL CHECK (status IN ('success', 'partial', 'fail', 'inconclusive')),
    yield_pct NUMERIC NULL CHECK (yield_pct IS NULL OR (yield_pct >= 0 AND yield_pct <= 100)),
    conditions_actual JSONB NULL,
    observations TEXT NULL,
    failure_reason TEXT NULL,
    recorded_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_by TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_reaction_outcomes_reaction
    ON reaction_outcomes (reaction_id, recorded_at DESC);

CREATE UNIQUE INDEX IF NOT EXISTS uq_reaction_outcomes_eln
    ON reaction_outcomes (eln_experiment_id)
    WHERE eln_experiment_id IS NOT NULL;
