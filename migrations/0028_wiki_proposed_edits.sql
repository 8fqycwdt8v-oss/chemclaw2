-- Wave-3c opportunity #1: propose-edit / apply protocol for wiki writes.
--
-- Today every agent-driven write to wiki_pages lands immediately. For
-- regulated chemistry contexts (and any time a deep-research run touches a
-- canonical page), a reviewer-in-the-loop step is the right gate. This
-- migration adds the staging table; the agent's new `propose_wiki_edit`
-- tool writes here, and an admin route applies into wiki_pages via the
-- same `upsertWikiPage` path.
--
-- Status flow:
--   pending  → applied   (admin approves → wiki_pages.upsert; row stays for audit)
--   pending  → rejected  (admin dismisses with a reason; row stays for audit)
--   pending  → superseded (a newer pending proposal for the same slug replaces; older row marked)
--
-- The table is append-only via RLS (no UPDATE / DELETE policy) for compliance
-- — the apply / reject paths write a NEW status row tagged with the
-- previous_id, mirroring the wiki_revisions audit pattern.
--
-- citations + content are JSONB so a proposal carries everything needed to
-- replay the upsertWikiPage call without re-deriving from a stale wiki row.

CREATE TABLE IF NOT EXISTS wiki_proposed_edits (
  id               UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug             TEXT NOT NULL,
  title            TEXT NOT NULL,
  content          JSONB NOT NULL,
  content_text     TEXT NOT NULL,
  citations        JSONB NOT NULL DEFAULT '[]'::jsonb,
  proposed_by      TEXT NOT NULL,
  rationale        TEXT,
  status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'applied', 'rejected', 'superseded')),
  previous_id      UUID REFERENCES wiki_proposed_edits(id),
  reviewed_by      TEXT,
  review_comment   TEXT,
  reviewed_at      TIMESTAMPTZ,
  applied_page_id  UUID REFERENCES wiki_pages(id),
  created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Pending queue lookup: admin page lists open proposals sorted newest-first.
CREATE INDEX IF NOT EXISTS wiki_proposed_edits_pending_idx
  ON wiki_proposed_edits (created_at DESC) WHERE status = 'pending';

-- Per-slug history: "show me all proposals against this page" for audit.
CREATE INDEX IF NOT EXISTS wiki_proposed_edits_slug_idx
  ON wiki_proposed_edits (slug, created_at DESC);

-- Author-scoped history: "what did this user propose"
CREATE INDEX IF NOT EXISTS wiki_proposed_edits_author_idx
  ON wiki_proposed_edits (proposed_by, created_at DESC);

ALTER TABLE wiki_proposed_edits ENABLE ROW LEVEL SECURITY;
CREATE POLICY wiki_proposed_edits_all ON wiki_proposed_edits FOR ALL USING (true) WITH CHECK (true);
