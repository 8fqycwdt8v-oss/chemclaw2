-- AI-Scientist quality gate: automated ensemble review of generated drafts.
--
-- `review_draft` (api/agent/tools_knowledge.py) runs an LLM-as-judge
-- ensemble (5 independent reviews + 1 meta-review, NeurIPS-rubric style,
-- per Nature s41586-026-10265-5) over a deep-research report or a
-- needs-review wiki draft *before* the agent commits it. The meta-review
-- is persisted here; the curator inbox surfaces rows whose decision is
-- not 'accept' as a fourth attention bucket.
--
-- Owner-scoped via created_by (CLAUDE.md owner-scoping rule). `page_slug`
-- / `investigation_id` are optional back-references to whatever the draft
-- will become; both nullable so a free-floating report review is allowed.
CREATE TABLE IF NOT EXISTS draft_reviews (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  kind             TEXT NOT NULL,                       -- 'report' | 'wiki'
  page_slug        TEXT,
  investigation_id UUID REFERENCES investigations(id) ON DELETE CASCADE,
  decision         TEXT NOT NULL
                   CHECK (decision IN ('accept', 'revise', 'reject')),
  overall          INTEGER NOT NULL,                    -- 1-10 consensus score
  summary          TEXT NOT NULL,
  top_issues       JSONB NOT NULL DEFAULT '[]'::jsonb,
  reviewer_scores  JSONB NOT NULL DEFAULT '[]'::jsonb,  -- the 5 individual reviews
  created_by       TEXT NOT NULL,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Hot path: the curator inbox lists a caller's non-accepted reviews,
-- newest first. New empty table — a plain CREATE INDEX is fine (no
-- CONCURRENTLY needed on a table with no rows).
CREATE INDEX IF NOT EXISTS draft_reviews_owner_idx
    ON draft_reviews (created_by, created_at DESC, id DESC);
