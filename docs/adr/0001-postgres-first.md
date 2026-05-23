# ADR 0001: Postgres for everything

## Status

Accepted (2026-04, pre-Python rewrite). Confirmed in the Python port.

## Context

The system stores: session state, chat history, wiki pages + revisions,
audit log, rate-limit buckets, fingerprints (Morgan / DRFP), embeddings
(pgvector), background-job queues, code-execution traces, world-model
entries, hypotheses + Elo ratings.

The textbook approach would split these across multiple data stores: a
session store (Redis), a queue (Redis/SQS/Celery), an analytics store
(Postgres), a vector DB (Pinecone/Weaviate), maybe a search index
(OpenSearch). chemclaw2 doesn't.

## Decision

One Postgres instance with pgvector. Everything above lives in tables.
Job queues use advisory locks. Vector search uses pgvector HNSW.
Full-text search uses Postgres FTS (with RRF fusion against pgvector
similarity for hybrid queries).

## Consequences

**Wins**
- One backup + restore story.
- One credentials boundary, one observability surface, one network hop
  from the app.
- pgvector + FTS are good enough at the data sizes we deploy. RRF
  fusion was a 50-line `api/db/queries/paper_chunks.py` change, not a
  multi-week Pinecone integration.
- New features cost a migration, not a service.

**Costs**
- Pool sizing matters more — every replica × every concern shares the
  same pool. `DB_POOL_SIZE` + `DB_POOL_MAX_OVERFLOW` are env-tunable.
- Vector queries scale to millions of rows, not billions. If/when we
  cross that, this ADR gets revisited.
- The "drop in Redis for rate limiting" intuition gets pushed back on
  every quarter. Advisory locks have been measured fine to 50 RPS on a
  shared-CPU-1x Fly Postgres.

## Triggers to revisit

- Median Postgres CPU pinning above 70 % sustained — first move a hot
  table to a read replica, then consider splitting.
- pgvector query latency > 100 ms p99 on the largest index — first tune
  HNSW (`m`, `ef_search`), then consider Qdrant/Weaviate for that one
  index only.
- Worker queue depth chronically > 5k items behind — first scale
  worker replicas; advisory locks fan out cleanly.
