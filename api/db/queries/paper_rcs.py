"""PaperQA2-style RCS (Re-ranking + Contextual Summarisation) for paper chunks.

`score_chunks_with_llm` is the public entry point. For each input chunk
(typically the output of `paper_chunks.hybrid_search_paper_chunks`), the
configured provider's chat model produces a 1-10 relevance score plus a
≤300-word query-conditioned summary, attached to the chunk dict.

Provider is chosen by `RCS_PROVIDER` env (`anthropic` default, also
`openai`). Both paths share the same prompt + JSON contract so swapping
providers is observable in eval but transparent to callers. The chosen
provider fails closed if its SDK / key is missing — no silent fallback,
so misconfiguration shows up as `rcs_error` rows in the eval log rather
than a quietly-degraded retrieval pipeline.

Reference: PaperQA2 (arXiv:2409.13740).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from typing import Any

logger = logging.getLogger(__name__)


RCS_PROMPT = """You are scoring the relevance of a paper excerpt to a research query.

Query: {query}

Paper: {title}
DOI: {doi}
Section: {section}
Excerpt:
\"\"\"
{excerpt}
\"\"\"

Reply with EXACTLY one JSON object inside a ```json fenced block. No prose before or after.

```json
{{"score": <integer 1-10>, "summary": "<≤300 word summary of what the excerpt says about the query>"}}
```

Score guide:
- 1-3: off-topic or contradicts the query
- 4-6: tangentially related; provides context but no direct answer
- 7-8: addresses the query partially or with caveats
- 9-10: directly and substantively answers the query"""


def _extract_json_object(text_body: str) -> str | None:
    """Extract the first balanced {...} JSON object from a model reply.

    Prefers a ```json fenced block when present (what RCS_PROMPT asks for);
    otherwise walks the string tracking brace depth, ignoring braces inside
    string literals. Returns None when no balanced object exists. Tolerant
    of preceding prose, trailing prose, and nested objects in the summary.
    """
    if not text_body:
        return None
    # Prefer the fenced block.
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text_body, re.DOTALL)
    if fence:
        return fence.group(1)
    # Fallback: balanced-brace scan.
    start = text_body.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text_body)):
        ch = text_body[i]
        if escape:
            escape = False
            continue
        if ch == "\\" and in_string:
            escape = True
            continue
        if ch == '"':
            in_string = not in_string
            continue
        if in_string:
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return text_body[start: i + 1]
    return None


# Module-level lazy clients matching the api/embeddings.py pattern. Each
# paper_qa call would otherwise construct a fresh client; reusing the
# instance avoids repeated TLS handshakes for sequential queries.
_anthropic_client: Any = None
_openai_client: Any = None


def _get_anthropic_client() -> Any:
    global _anthropic_client
    if _anthropic_client is not None:
        return _anthropic_client
    from anthropic import AsyncAnthropic
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return None
    _anthropic_client = AsyncAnthropic(api_key=api_key)
    return _anthropic_client


def _get_openai_client() -> Any:
    global _openai_client
    if _openai_client is not None:
        return _openai_client
    from openai import AsyncOpenAI
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        return None
    _openai_client = AsyncOpenAI(api_key=api_key)
    return _openai_client


async def _rcs_via_anthropic(
    client: Any,
    model: str,
    prompt: str,
    c: dict[str, Any],
) -> tuple[str | None, str | None]:
    """Return (text_body, rcs_error). text_body=None on failure."""
    try:
        resp = await client.messages.create(
            model=model,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        logger.warning("rcs_anthropic_call_failed chunk=%s err=%s", c.get("id"), e)
        return None, "LLM call failed"
    text_body = ""
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            text_body += getattr(block, "text", "")
    return text_body, None


async def _rcs_via_openai(
    client: Any,
    model: str,
    prompt: str,
    c: dict[str, Any],
) -> tuple[str | None, str | None]:
    """OpenAI Chat Completions equivalent — same prompt, same JSON contract."""
    try:
        resp = await client.chat.completions.create(
            model=model,
            max_tokens=600,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        logger.warning("rcs_openai_call_failed chunk=%s err=%s", c.get("id"), e)
        return None, "LLM call failed"
    try:
        text_body = resp.choices[0].message.content or ""
    except (AttributeError, IndexError):
        logger.warning(
            "openai_response_shape_unexpected chunk=%s",
            c.get("id"),
        )
        return None, "openai response shape unexpected"
    return text_body, None


async def score_chunks_with_llm(
    chunks: list[dict[str, Any]],
    query: str,
    max_concurrency: int = 4,
) -> list[dict[str, Any]]:
    """Apply PaperQA2-style RCS to each chunk.

    For each chunk, prompt the LLM with (chunk, query) to produce:
      - relevance_score: integer 1-10
      - summary: ≤300 word query-conditioned synopsis

    Failures (LLM error, malformed JSON) attach `rcs_error` and leave the
    chunk's other fields intact. Caller can filter on `relevance_score`
    presence to drop those.

    Provider is picked from `RCS_PROVIDER` env (default: `anthropic`).
    Both paths use the same prompt + JSON contract. Models override via
    `ANTHROPIC_RCS_MODEL` / `OPENAI_RCS_MODEL`. The chosen provider
    fails closed when its key/SDK is absent — no silent fallback to the
    other provider so misconfiguration is visible.
    """
    if not chunks:
        return []
    provider = os.environ.get("RCS_PROVIDER", "anthropic").strip().lower()
    if provider not in ("anthropic", "openai"):
        logger.warning("invalid RCS_PROVIDER=%r — defaulting to anthropic", provider)
        provider = "anthropic"

    if provider == "anthropic":
        try:
            import anthropic  # noqa: F401 — import-side import-check only
        except ImportError:
            logger.warning("anthropic_unavailable for RCS — returning chunks unscored")
            return [{**c, "rcs_error": "anthropic SDK not installed"} for c in chunks]
        client = _get_anthropic_client()
        if client is None:
            logger.warning("ANTHROPIC_API_KEY missing — returning chunks unscored")
            return [{**c, "rcs_error": "ANTHROPIC_API_KEY not configured"} for c in chunks]
        model = os.environ.get("ANTHROPIC_RCS_MODEL", "claude-haiku-4-5-20251001")
    else:  # openai
        try:
            import openai  # noqa: F401
        except ImportError:
            logger.warning("openai_unavailable for RCS — returning chunks unscored")
            return [{**c, "rcs_error": "openai SDK not installed"} for c in chunks]
        client = _get_openai_client()
        if client is None:
            logger.warning("OPENAI_API_KEY missing — returning chunks unscored")
            return [{**c, "rcs_error": "OPENAI_API_KEY not configured"} for c in chunks]
        # Default kept generic so we don't pin a model that may be deprecated;
        # explicit OPENAI_RCS_MODEL is recommended in production.
        model = os.environ.get("OPENAI_RCS_MODEL", "gpt-4o-mini")

    sem = asyncio.Semaphore(max_concurrency)

    async def score_one(c: dict[str, Any]) -> dict[str, Any]:
        prompt = RCS_PROMPT.format(
            query=query,
            title=c.get("title") or "(unknown)",
            doi=c.get("doi") or "(no DOI)",
            section=c.get("section") or "(no section)",
            excerpt=c["text"][:4000],
        )
        async with sem:
            if provider == "anthropic":
                text_body, err = await _rcs_via_anthropic(client, model, prompt, c)
            else:
                text_body, err = await _rcs_via_openai(client, model, prompt, c)
        if err is not None or text_body is None:
            return {**c, "rcs_error": err or "no response"}
        # Prefer ```json fenced extraction, fall back to balanced-brace scan
        # so nested braces in the summary don't truncate the parse.
        raw = _extract_json_object(text_body)
        if raw is None:
            return {**c, "rcs_error": "no JSON in LLM response"}
        try:
            parsed = json.loads(raw)
        except Exception:
            return {**c, "rcs_error": "JSON parse failed"}
        score_val = parsed.get("score")
        summary = parsed.get("summary")
        if not isinstance(score_val, (int, float)):
            return {**c, "rcs_error": "score missing or non-numeric"}
        score_int = max(1, min(10, int(round(score_val))))
        return {
            **c,
            "relevance_score": score_int,
            "summary": str(summary or "")[:1500],
        }

    return await asyncio.gather(*(score_one(c) for c in chunks))
