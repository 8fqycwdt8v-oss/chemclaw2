CREATE TABLE wiki_pages (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  slug         TEXT UNIQUE NOT NULL,
  title        TEXT NOT NULL,
  content      JSONB NOT NULL DEFAULT '{}',
  content_text TEXT,
  created_by   TEXT NOT NULL,
  updated_by   TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  updated_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  version      INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE wiki_chunks (
  id        UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  page_id   UUID NOT NULL REFERENCES wiki_pages(id) ON DELETE CASCADE,
  chunk_idx INTEGER NOT NULL,
  text      TEXT NOT NULL,
  embedding vector(1536)
);

CREATE INDEX wiki_chunks_embedding_hnsw ON wiki_chunks
  USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);

CREATE TABLE wiki_citations (
  id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  page_id     UUID NOT NULL REFERENCES wiki_pages(id) ON DELETE CASCADE,
  citation_id TEXT NOT NULL,
  source_type TEXT NOT NULL,
  source_id   TEXT,
  label       TEXT NOT NULL
);

CREATE INDEX wiki_pages_fts ON wiki_pages
  USING gin (to_tsvector('english', coalesce(content_text, '')));

CREATE FUNCTION update_wiki_updated_at() RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  NEW.version = OLD.version + 1;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER wiki_pages_auto_version
  BEFORE UPDATE ON wiki_pages
  FOR EACH ROW EXECUTE FUNCTION update_wiki_updated_at();
