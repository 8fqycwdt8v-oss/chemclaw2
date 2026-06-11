"""OpenAI text-embedding-3-small client.

Shared by api/routes/wiki.py, api/agent/tools.py (semantic lookup), and
api/workers/campaign_worker.py (campaign-wiki upsert). Each module previously
imported `embed_texts` from `api/routes/wiki.py`, which forced a route module
to be importable from a worker process — moving it here breaks that cycle.
"""
from __future__ import annotations

import os

from openai import AsyncOpenAI

EMBED_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536  # text-embedding-3-small returns 1536-dim vectors

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI:
    global _client
    if _client is None:
        # Explicit timeout: the SDK default is 600 s, which would let one hung
        # embedding call stall a wiki upsert or document ingest for 10 minutes.
        # max_retries=3 keeps the SDK's built-in exponential backoff for
        # transient 429/5xx (default is 2).
        _client = AsyncOpenAI(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            base_url=os.environ.get("OPENAI_BASE_URL") or None,
            timeout=30.0,
            max_retries=3,
        )
    return _client


async def embed_texts(texts: list[str]) -> list[list[float]]:
    resp = await _get_client().embeddings.create(model=EMBED_MODEL, input=texts)
    return [d.embedding for d in resp.data]
