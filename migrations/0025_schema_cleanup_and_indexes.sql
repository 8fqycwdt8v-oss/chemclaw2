-- Wave-1 E1: drop unused bi-temporal columns on entity tables.
-- Migration 0013 added valid_from/valid_to to compounds, reactions, wiki_pages
-- but only the wiki side ever got wired up (v2.1-B1 pointInTimeWiki uses the
-- wiki_revisions table, but the GET ?asOf= path is the lone reader). The
-- compounds/reactions columns are dead code — no read query references them,
-- no write path sets valid_to. BACKLOG flagged them in two places (L15, L26)
-- as either-wire-or-drop. We drop now because the right path for entity
-- temporality is the audit_log + revisions pattern, not parallel columns.
ALTER TABLE compounds DROP COLUMN IF EXISTS valid_from;
ALTER TABLE compounds DROP COLUMN IF EXISTS valid_to;
ALTER TABLE reactions DROP COLUMN IF EXISTS valid_from;
ALTER TABLE reactions DROP COLUMN IF EXISTS valid_to;

-- Wave-1 D3: index gaps flagged in BACKLOG (L12, L28).
--
-- wiki_pages: keyset pagination over listWikiPages uses
-- ORDER BY updated_at DESC, id DESC; without the composite the planner falls
-- back to a sort over the full table once the result set grows. 0020 added
-- (updated_at, id) but the ASC order; here we add the DESC composite that
-- matches the actual ORDER BY in queries/wiki.ts.
CREATE INDEX IF NOT EXISTS wiki_pages_updated_id_desc_idx
  ON wiki_pages (updated_at DESC, id DESC);

-- wiki_subscriptions: countUnreadSubscriptions joins on (user_id, page_id)
-- and is called on every app-layout render. 0014 already indexed user_id
-- alone; this adds the join-covering composite so the lookup is index-only.
CREATE INDEX IF NOT EXISTS wiki_subscriptions_user_page_idx
  ON wiki_subscriptions (user_id, page_id);
