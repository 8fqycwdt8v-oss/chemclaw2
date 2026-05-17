-- Wave-2a: knowledge persistence layer.
--
-- Today, results from eln_fetch / web_search / fetch_document only live in the
-- per-session agent_sessions.entries JSONB and disappear once a session is
-- pruned. The audit flagged this as a knowledge leak: each call re-fetches
-- the same external knowledge and the org's collective tool-call output never
-- becomes world-state. Same story for chemistry properties extracted from
-- prose into wiki pages — they exist as text only, not as queryable rows.
--
-- This migration adds three persistence tables plus one perf primitive:
--   - external_facts  — caches tool-fetch results keyed by (source_type, source_id)
--   - properties      — SAR-style typed rows attached to a compound
--   - papers          — first-class document entity with title/DOI/abstract
--   - morgan_fp_popcount — generated column on compounds for Tanimoto re-rank


-- external_facts: world-state cache for tool fetches. Upsert from
-- eln_fetch / web_search / fetch_document; the next call reads from here
-- when the row is fresh and the upstream domain is offline / rate-limited.
CREATE TABLE IF NOT EXISTS external_facts (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_type  TEXT NOT NULL,    -- 'eln' | 'web_search' | 'doc' | future: 'pubmed' | 'crossref'
  source_id    TEXT NOT NULL,    -- experiment id / normalized query / canonical URL
  payload      JSONB NOT NULL,   -- whatever the tool returned (the agent's view)
  content_text TEXT,             -- searchable extract for FTS
  first_seen   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  last_seen    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  fetched_by   TEXT NOT NULL,
  UNIQUE (source_type, source_id)
);
CREATE INDEX IF NOT EXISTS external_facts_lookup_idx
  ON external_facts (source_type, source_id);
CREATE INDEX IF NOT EXISTS external_facts_fts_idx
  ON external_facts USING gin (to_tsvector('english', coalesce(content_text, '')));
ALTER TABLE external_facts ENABLE ROW LEVEL SECURITY;
CREATE POLICY external_facts_all ON external_facts FOR ALL USING (true) WITH CHECK (true);


-- properties: structure-activity / measured-property rows attached to a
-- compound. Replaces the today-pattern of "embed the value in markdown
-- prose inside a wiki page, lose ability to query". Either value_num OR
-- value_text must be set; method + source_citation_id let the agent trace
-- a measurement back to its origin.
CREATE TABLE IF NOT EXISTS properties (
  id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  compound_id         UUID NOT NULL REFERENCES compounds(id) ON DELETE CASCADE,
  name                TEXT NOT NULL,             -- 'yield' | 'logP' | 'pIC50' | …
  value_num           DOUBLE PRECISION,
  value_text          TEXT,
  unit                TEXT,                      -- '%' | 'M' | 'nM' | …
  method              TEXT,                      -- 'HPLC' | 'calc:Crippen' | …
  source_citation_id  TEXT,                      -- ties back to wiki_citations.citation_id
  measured_at         TIMESTAMPTZ,
  created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_by          TEXT NOT NULL,
  CHECK (value_num IS NOT NULL OR value_text IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS properties_compound_name_idx ON properties (compound_id, name);
CREATE INDEX IF NOT EXISTS properties_name_idx          ON properties (name);
ALTER TABLE properties ENABLE ROW LEVEL SECURITY;
CREATE POLICY properties_all ON properties FOR ALL USING (true) WITH CHECK (true);


-- papers: first-class document entity. Today, doc references in wiki
-- citations are opaque text; this table lets the system promote a frequently-
-- cited paper to a structured row with DOI / PubMed id / abstract that the
-- agent can query directly. Compatible with wiki_citations.sourceType='doc' —
-- the wiki citation's sourceId can point to papers.id when known.
CREATE TABLE IF NOT EXISTS papers (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  doi          TEXT,
  pubmed_id    TEXT,
  url          TEXT,
  title        TEXT NOT NULL,
  abstract     TEXT,
  content_text TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  created_by   TEXT
);
-- Partial unique indexes — many papers will have DOI, some only pubmed_id;
-- a full UNIQUE on a nullable column would block all-NULL duplicates only.
CREATE UNIQUE INDEX IF NOT EXISTS papers_doi_unique_idx
  ON papers (doi) WHERE doi IS NOT NULL;
CREATE UNIQUE INDEX IF NOT EXISTS papers_pubmed_unique_idx
  ON papers (pubmed_id) WHERE pubmed_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS papers_fts_idx
  ON papers USING gin (
    to_tsvector('english', coalesce(title, '') || ' ' || coalesce(abstract, ''))
  );
ALTER TABLE papers ENABLE ROW LEVEL SECURITY;
CREATE POLICY papers_all ON papers FOR ALL USING (true) WITH CHECK (true);


-- Wave-2a opportunity #4: generated popcount column on compounds.
-- compound_similarity_search re-ranks with Tanimoto = bit_count(a & b)::float
-- / bit_count(a | b). The denominator factors out bit_count(a) and
-- bit_count(b) — caching bit_count(morgan_fp) at write time removes one
-- bit_count call per re-ranked row at query time.
ALTER TABLE compounds
  ADD COLUMN IF NOT EXISTS morgan_fp_popcount INTEGER
  GENERATED ALWAYS AS (bit_count(morgan_fp)::int) STORED;
