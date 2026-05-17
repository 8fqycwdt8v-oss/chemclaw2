-- Per-step approval gate for campaigns. Default false so existing campaigns
-- run as-before; kickoff_campaign with approval:'per_step' flips non-first
-- steps to true so the worker poll skips them until explicitly approved.
ALTER TABLE campaign_steps
  ADD COLUMN IF NOT EXISTS requires_approval BOOLEAN NOT NULL DEFAULT false;

-- Wiki page subscriptions: a user watches a page; the UI surfaces a nav badge
-- with the count of watched pages that have a newer version than last_seen_version.
-- No notification channel — surfacing is pull-on-page-load.
CREATE TABLE IF NOT EXISTS wiki_subscriptions (
  user_id            TEXT      NOT NULL,
  page_id            UUID      NOT NULL REFERENCES wiki_pages(id) ON DELETE CASCADE,
  last_seen_version  INTEGER   NOT NULL DEFAULT 0,
  created_at         TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  PRIMARY KEY (user_id, page_id)
);

CREATE INDEX IF NOT EXISTS wiki_subscriptions_user_idx ON wiki_subscriptions (user_id);

-- RLS: allow each user to see and write only their own subscriptions.
-- Application-level filtering uses Clerk userId; this RLS bodies are stub-permissive
-- to match the existing pattern (per-tenant RLS deferred until multi-tenant).
ALTER TABLE wiki_subscriptions ENABLE ROW LEVEL SECURITY;
CREATE POLICY wiki_subscriptions_all ON wiki_subscriptions FOR ALL USING (true) WITH CHECK (true);
