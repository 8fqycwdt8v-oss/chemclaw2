-- Schema invariants: encode contracts the app layer enforces so a second
-- writer (psql, restore, future service) can't introduce dirty state.
--
-- Status / period / maturity values come from string enums in TypeScript;
-- DOMAINs would be cleaner but require migration coordination across types,
-- so we use named CHECK constraints — equivalent semantics, easier to add
-- now without touching column types.
--
-- All ADDs are guarded with NOT VALID / VALIDATE? No — these constraints
-- must hold at apply time; CI runs against a fresh DB so there are no dirty
-- rows. Operators applying to long-running production should pre-flight:
--   SELECT DISTINCT status FROM synthesis_campaigns;
--   SELECT DISTINCT status FROM campaign_steps;
--   SELECT DISTINCT maturity FROM wiki_pages;
--   SELECT DISTINCT period FROM project_budgets;
-- and remediate any outliers before applying.

-- synthesis_campaigns.status — see TERMINAL/NON_TERMINAL exports in
-- queries/campaigns.ts. The two state spaces are disjoint and exhaustive.
ALTER TABLE synthesis_campaigns
  ADD CONSTRAINT synthesis_campaigns_status_chk
  CHECK (status IN ('planning', 'awaiting_input', 'running', 'complete', 'failed'));

-- campaign_steps.status — 'complete' and 'failed' are terminal; 'pending'
-- and 'running' transition through markStepComplete / markStepFailed.
ALTER TABLE campaign_steps
  ADD CONSTRAINT campaign_steps_status_chk
  CHECK (status IN ('pending', 'running', 'complete', 'failed'));

-- markStepFailed already clamps to [0,10]; the column-level guard catches
-- direct DB writes that bypass the helper.
ALTER TABLE campaign_steps
  ADD CONSTRAINT campaign_steps_retry_chk
  CHECK (retry_count >= 0 AND retry_count <= 10);

-- (campaign_id, step_idx) is the natural key of a step. addCampaignStep
-- catches the unique-violation and converts to a "step already exists" no-op
-- so idempotent re-confirm flows keep working.
ALTER TABLE campaign_steps
  ADD CONSTRAINT campaign_steps_unique_step
  UNIQUE (campaign_id, step_idx);

-- Speeds the retry sweep in workers/fp-worker/src/campaign-worker.ts. Partial
-- index keeps it tiny — only 'failed' rows participate in the lookup.
CREATE INDEX IF NOT EXISTS campaign_steps_status_retry_idx
  ON campaign_steps (status, next_retry_at)
  WHERE status = 'failed';

-- wiki_pages.maturity — UI mode that drives review-queue routing.
ALTER TABLE wiki_pages
  ADD CONSTRAINT wiki_pages_maturity_chk
  CHECK (maturity IN ('exploratory', 'validated', 'authoritative'));

-- project_budgets.period — BudgetPeriod TypeScript type matches.
ALTER TABLE project_budgets
  ADD CONSTRAINT project_budgets_period_chk
  CHECK (period IN ('day', 'week', 'month'));

-- Non-negative caps: app validates this at PUT time; constraint backstops
-- any non-app writer. NULL means "no cap" — stays allowed.
ALTER TABLE project_budgets
  ADD CONSTRAINT project_budgets_caps_nonneg_chk
  CHECK (
    (tool_calls_cap  IS NULL OR tool_calls_cap  >= 0)
    AND (experiments_cap IS NULL OR experiments_cap >= 0)
    AND (tokens_cap      IS NULL OR tokens_cap      >= 0)
  );

-- properties row must carry at least one of (value_num, value_text). Mirrors
-- the registerProperty validator; protects against silently-empty SAR rows.
ALTER TABLE properties
  ADD CONSTRAINT properties_value_present_chk
  CHECK (value_num IS NOT NULL OR value_text IS NOT NULL);
