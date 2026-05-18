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
        _client = AsyncOpenAI(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            base_url=os.environ.get("OPENAI_BASE_URL") or None,
        )
    return _client


async def embed_texts(texts: list[str]) -> list[list[float]]:
    resp = await _get_client().embeddings.create(model=EMBED_MODEL, input=texts)
    return [d.embedding for d in resp.data]
