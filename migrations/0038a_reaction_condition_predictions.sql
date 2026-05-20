-- Cache for reaction-condition predictions produced by the mcp-rxn-conditions
-- MCP server (RXN4Chemistry-backed) or by neighbor aggregation. Keeping
-- predictions in a separate table from `reactions` lets us:
--
--   - hold multiple predictions per reaction (different models, A/B comparison)
--   - separate prediction from "what the chemist actually used" via the
--     used_in_step_id link to campaign_steps
--   - record predictions for reactions not yet inserted (reaction_id NULL,
--     rxn_smiles populated) — e.g. retrosynthesis intermediates the agent
--     considers without registering
--
-- Dedup key: (reaction_id, model) where reaction_id is non-null. Predictions
-- without a reaction_id fall through to the rxn_smiles index for cache lookups.

CREATE TABLE IF NOT EXISTS reaction_condition_predictions (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    reaction_id     UUID REFERENCES reactions(id) ON DELETE CASCADE,
    rxn_smiles      TEXT NOT NULL,
    drfp_bits       BIT(2048),
    conditions      JSONB NOT NULL,
    model           TEXT NOT NULL,
    confidence      DOUBLE PRECISION,
    source          TEXT NOT NULL,
    used_in_step_id UUID REFERENCES campaign_steps(id) ON DELETE SET NULL,
    created_by      TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE UNIQUE INDEX IF NOT EXISTS reaction_condition_predictions_dedupe
    ON reaction_condition_predictions (reaction_id, model)
    WHERE reaction_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS reaction_condition_predictions_rxn_smiles
    ON reaction_condition_predictions (rxn_smiles);
