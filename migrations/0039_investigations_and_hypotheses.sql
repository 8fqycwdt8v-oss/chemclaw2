-- Phase B: investigations, world model, hypotheses, hypothesis rankings.
--
-- Three coupled additions for the AI-scientist surface (analysis writeup in
-- BACKLOG Phase A follow-ups -> Phase B):
--
-- 1. `investigations` — a long-horizon research thread that outlives any
--    single chat session. Holds the open-ended objective. Owner-scoped via
--    created_by. Session_id is nullable so an investigation can survive
--    session resumption / cleanup.
--
-- 2. `world_model_entries` — Kosmos-style structured persistent state.
--    Atomic facts / assumptions / open_questions / evidence keyed by
--    investigation. One row per claim so the agent can supersede / close
--    individually without rewriting a blob.
--
-- 3. `hypotheses` + `hypothesis_rankings` — Google Co-Scientist's
--    Generation+Reflection+Ranking+Evolution primitives. `parent_id` is
--    the Evolution chain; `elo_rating` is the tournament-ranking signal.
--    `hypothesis_rankings` is the pairwise audit log; eager-updates on
--    `hypotheses.elo_rating` keep the hot read path fast.
--
-- All four tables denormalize `created_by` (per CLAUDE.md owner-scoping
-- rule — every per-user UPDATE/DELETE predicates on user_id; child tables
-- carry the same column so we don't need to JOIN through investigations).

CREATE TABLE IF NOT EXISTS investigations (
  id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  session_id TEXT,                                -- nullable; investigations outlive sessions
  title      TEXT NOT NULL,
  objective  TEXT NOT NULL,
  status     TEXT NOT NULL DEFAULT 'active'
             CHECK (status IN ('active', 'paused', 'complete')),
  created_by TEXT NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS investigations_owner_idx
    ON investigations (created_by, updated_at DESC, id DESC);

CREATE INDEX IF NOT EXISTS investigations_session_idx
    ON investigations (session_id)
    WHERE session_id IS NOT NULL;


CREATE TABLE IF NOT EXISTS world_model_entries (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  investigation_id UUID NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
  kind             TEXT NOT NULL
                   CHECK (kind IN ('fact', 'assumption', 'open_question', 'evidence')),
  content          TEXT NOT NULL,
  payload          JSONB NOT NULL DEFAULT '{}'::jsonb,
  confidence       DOUBLE PRECISION
                   CHECK (confidence IS NULL OR (confidence >= 0 AND confidence <= 1)),
  status           TEXT NOT NULL DEFAULT 'active'
                   CHECK (status IN ('active', 'superseded', 'closed')),
  created_by       TEXT NOT NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS world_model_investigation_idx
    ON world_model_entries (investigation_id, kind, status, updated_at DESC);

CREATE INDEX IF NOT EXISTS world_model_fts_idx
    ON world_model_entries USING GIN (to_tsvector('english', content));


CREATE TABLE IF NOT EXISTS hypotheses (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  investigation_id UUID NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
  parent_id        UUID REFERENCES hypotheses(id) ON DELETE SET NULL,  -- evolution chain
  statement        TEXT NOT NULL,
  rationale        TEXT,
  status           TEXT NOT NULL DEFAULT 'proposed'
                   CHECK (status IN ('proposed', 'ranked', 'refined', 'retired')),
  elo_rating       DOUBLE PRECISION NOT NULL DEFAULT 1000.0,
  created_by       TEXT NOT NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS hypotheses_investigation_rank_idx
    ON hypotheses (investigation_id, status, elo_rating DESC);

CREATE INDEX IF NOT EXISTS hypotheses_parent_idx
    ON hypotheses (parent_id)
    WHERE parent_id IS NOT NULL;


CREATE TABLE IF NOT EXISTS hypothesis_rankings (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  investigation_id UUID NOT NULL REFERENCES investigations(id) ON DELETE CASCADE,
  hypothesis_a_id  UUID NOT NULL REFERENCES hypotheses(id) ON DELETE CASCADE,
  hypothesis_b_id  UUID NOT NULL REFERENCES hypotheses(id) ON DELETE CASCADE,
  winner           TEXT NOT NULL CHECK (winner IN ('a', 'b', 'tie')),
  reason           TEXT,
  decided_by       TEXT NOT NULL,  -- user_id, or 'agent:reflection' / 'agent:debate'
  decided_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  CHECK (hypothesis_a_id <> hypothesis_b_id)
);

CREATE INDEX IF NOT EXISTS hypothesis_rankings_investigation_idx
    ON hypothesis_rankings (investigation_id, decided_at DESC);
