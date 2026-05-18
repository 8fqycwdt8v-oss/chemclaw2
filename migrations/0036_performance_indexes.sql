-- Performance indexes: GIN FTS indexes and composite indexes for common queries.
-- All indexes use IF NOT EXISTS so the migration is idempotent.

-- GIN full-text search indexes — avoids runtime to_tsvector() on every query.
CREATE INDEX IF NOT EXISTS papers_fts_idx ON papers USING GIN (
    to_tsvector('english',
        coalesce(title, '') || ' ' ||
        coalesce(abstract, '') || ' ' ||
        coalesce(content_text, '')
    )
);

CREATE INDEX IF NOT EXISTS external_facts_fts_idx ON external_facts USING GIN (
    to_tsvector('english', coalesce(content_text, ''))
);

CREATE INDEX IF NOT EXISTS wiki_pages_fts_idx ON wiki_pages USING GIN (
    to_tsvector('english', coalesce(content_text, ''))
);

-- Feedback: session + user lookups.
CREATE INDEX IF NOT EXISTS agent_feedback_session_user_idx
    ON agent_feedback (session_id, user_id);

-- Todos: session + user lookups ordered by position.
CREATE INDEX IF NOT EXISTS agent_todos_session_user_idx
    ON agent_todos (session_id, user_id, position);

-- Campaign steps: campaign + status lookups (worker hot path).
CREATE INDEX IF NOT EXISTS campaign_steps_campaign_status_idx
    ON campaign_steps (campaign_id, status);

-- Campaign steps: retry eligibility filter (status=failed, retry_count<3, next_retry_at).
CREATE INDEX IF NOT EXISTS campaign_steps_retry_eligible_idx
    ON campaign_steps (next_retry_at)
    WHERE status = 'failed' AND retry_count < 3;

-- Campaigns: keyset pagination for list_user_campaigns.
CREATE INDEX IF NOT EXISTS synthesis_campaigns_user_updated_id_idx
    ON synthesis_campaigns (created_by, updated_at DESC, id DESC);

-- Notifications: unread filter (partial index — WHERE read = FALSE covers the hot path).
CREATE INDEX IF NOT EXISTS notifications_user_unread_idx
    ON notifications (user_id, created_at DESC)
    WHERE read = FALSE;

-- Notifications: full user+created_at for history queries.
CREATE INDEX IF NOT EXISTS notifications_user_created_idx
    ON notifications (user_id, created_at DESC);

-- Wiki revisions: page + version lookups.
CREATE INDEX IF NOT EXISTS wiki_revisions_page_version_idx
    ON wiki_revisions (page_id, version);

-- Wiki revisions: page + updated_at for asOf queries.
CREATE INDEX IF NOT EXISTS wiki_revisions_page_updated_idx
    ON wiki_revisions (page_id, updated_at DESC);
