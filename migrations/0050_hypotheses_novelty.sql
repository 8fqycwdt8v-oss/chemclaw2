-- AI-Scientist quality gate: novelty annotation on hypotheses.
--
-- `check_hypothesis_novelty` (api/agent/tools_investigation.py) compares a
-- candidate hypothesis against the indexed knowledge base (paper chunks +
-- wiki) before it enters the Elo tournament, mirroring the literature
-- novelty filter in Nature s41586-026-10265-5. The agent attaches the
-- result here via `propose_hypothesis(novelty=...)` so the tournament view
-- can flag claims that closely resemble existing work.
--
-- Shape: {label: novel|incremental|known, closest_prior, rationale,
-- related: [...], checked_at}. Nullable catalog-only default — no table
-- rewrite on a populated table.
ALTER TABLE hypotheses
    ADD COLUMN IF NOT EXISTS novelty JSONB;
