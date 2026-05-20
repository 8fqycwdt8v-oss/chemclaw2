-- Paper full-text RAG support (PaperQA2-style).
--
-- Until now, `papers` stored metadata + raw content_text only. To answer
-- "what does the literature say about X?" the agent had to lean on FTS
-- across the full content_text blob, which is coarse and misses semantic
-- paraphrases. paper_chunks splits paper bodies into addressable chunks
-- with embeddings + per-chunk FTS, mirroring wiki_chunks' shape so the
-- existing hybrid-search pattern from api/db/queries/wiki_read.py
-- transfers cleanly. Each chunk also stores its section heading and
-- 0-based page so citations can resolve back to a precise location.

CREATE TABLE IF NOT EXISTS paper_chunks (
  id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  paper_id     UUID NOT NULL REFERENCES papers(id) ON DELETE CASCADE,
  chunk_idx    INTEGER NOT NULL,           -- position within the paper body
  section      TEXT,                       -- nearest heading; nullable
  page         INTEGER,                    -- 0-indexed page; nullable for plain-text ingest
  text         TEXT NOT NULL,
  embedding    vector(1536),               -- nullable; populated on ingest
  created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
  UNIQUE (paper_id, chunk_idx)
);

CREATE INDEX IF NOT EXISTS paper_chunks_paper_idx
    ON paper_chunks (paper_id, chunk_idx);

CREATE INDEX IF NOT EXISTS paper_chunks_fts_idx
    ON paper_chunks USING GIN (to_tsvector('english', text));

-- HNSW index for semantic search. Deferred for now: chunks are inserted
-- in bursts during paper ingest and the table starts empty; build the
-- index once row counts justify it (matches the wiki_chunks pattern).
