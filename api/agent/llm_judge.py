"""Thin LLM-as-judge helper shared by the quality-gate tools.

This is the single `messages.create` wrapper behind the four
AI-Scientist-inspired quality gates (figure critique, draft review,
citation support check, hypothesis novelty check). It deliberately does
*nothing* beyond:

  - resolve a (provider, model) pair from env for a given `kind`
    ('text' judging vs. 'vision' critique),
  - issue one chat completion (optionally with base64 PNG images),
  - hand the raw text back through `_extract_json_object` + `json.loads`
    so callers get a parsed dict.

It does NOT manage retries, ensembling, routing, or any orchestration —
that lives in the calling tools. Keeping it a one-call wrapper is
intentional: CLAUDE.md forbids an LLM proxy / orchestration framework as
an anti-feature. Client construction and JSON extraction are *reused*
from `api.db.queries.paper_rcs` (the RCS reranker) rather than re-copied,
per the third-copy rule.

Provider/model selection mirrors the RCS pattern:
  - text judge: `JUDGE_PROVIDER` (default 'anthropic'),
    `ANTHROPIC_JUDGE_MODEL` (default Haiku), `OPENAI_JUDGE_MODEL`
    (default 'gpt-4o-mini').
  - vision critic: `VLM_PROVIDER` (default 'openai'), `VLM_MODEL`
    (default 'gpt-4o-mini') — the cheap vision tier the AI-Scientist
    paper used (GPT-4o) for plot critique, downsized to mini since the
    codebase already pays for it via RCS.

Every path fails *soft* — returns `(None, error_str)` — because these are
quality gates, not security gates. Callers log + fall open. The error
string never contains provider internals (CLAUDE.md security-4).
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

from api.db.queries.paper_rcs import (
    _extract_json_object,
    _get_anthropic_client,
    _get_openai_client,
)

logger = logging.getLogger(__name__)


def resolve_judge_model(kind: str) -> tuple[str, str]:
    """Resolve (provider, model) for a judging `kind` from env.

    `kind` is 'text' (default judging) or 'vision' (figure critique).
    Vision defaults to OpenAI gpt-4o-mini (cheap + vision-capable);
    text defaults to Anthropic Haiku (matching the RCS default). Both
    providers support image inputs, so the split is about cost defaults,
    not capability.
    """
    if kind == "vision":
        provider = os.environ.get("VLM_PROVIDER", "openai").strip().lower()
        if provider == "anthropic":
            return provider, os.environ.get(
                "ANTHROPIC_JUDGE_MODEL", "claude-haiku-4-5-20251001"
            )
        return "openai", os.environ.get("VLM_MODEL", "gpt-4o-mini")
    provider = os.environ.get("JUDGE_PROVIDER", "anthropic").strip().lower()
    if provider == "openai":
        return provider, os.environ.get("OPENAI_JUDGE_MODEL", "gpt-4o-mini")
    return "anthropic", os.environ.get("ANTHROPIC_JUDGE_MODEL", "claude-haiku-4-5-20251001")


async def _judge_anthropic(
    client: Any, model: str, prompt: str, images: list[str], max_tokens: int,
) -> tuple[str | None, str | None]:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for b64 in images:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": b64},
        })
    try:
        resp = await client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": content}],
        )
    except Exception as e:
        logger.warning("judge_anthropic_call_failed model=%s err=%s", model, e)
        return None, "LLM call failed"
    text_body = ""
    for block in resp.content:
        if getattr(block, "type", None) == "text":
            text_body += getattr(block, "text", "")
    return text_body, None


async def _judge_openai(
    client: Any, model: str, prompt: str, images: list[str], max_tokens: int,
) -> tuple[str | None, str | None]:
    content: list[dict[str, Any]] = [{"type": "text", "text": prompt}]
    for b64 in images:
        content.append({
            "type": "image_url",
            "image_url": {"url": f"data:image/png;base64,{b64}"},
        })
    try:
        resp = await client.chat.completions.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": content}],
        )
    except Exception as e:
        logger.warning("judge_openai_call_failed model=%s err=%s", model, e)
        return None, "LLM call failed"
    try:
        text_body = resp.choices[0].message.content or ""
    except (AttributeError, IndexError):
        logger.warning("judge_openai_response_shape_unexpected model=%s", model)
        return None, "response shape unexpected"
    return text_body, None


async def judge_json(
    prompt: str,
    *,
    provider: str,
    model: str,
    images: list[str] | None = None,
    max_tokens: int = 800,
) -> tuple[dict[str, Any] | None, str | None]:
    """Run one judging call and parse a single JSON object from the reply.

    Returns `(parsed_dict, None)` on success or `(None, error_str)` on any
    failure (missing key/SDK, LLM error, no JSON, parse error). Fails soft
    — the caller decides how to fall open. `images` is a list of base64
    PNG strings for the vision path.
    """
    imgs = images or []
    if provider == "anthropic":
        try:
            import anthropic  # noqa: F401
        except ImportError:
            logger.warning("anthropic SDK unavailable for judge")
            return None, "anthropic SDK not installed"
        client = _get_anthropic_client()
        if client is None:
            logger.warning("ANTHROPIC_API_KEY missing for judge")
            return None, "ANTHROPIC_API_KEY not configured"
        text_body, err = await _judge_anthropic(client, model, prompt, imgs, max_tokens)
    else:  # openai
        try:
            import openai  # noqa: F401
        except ImportError:
            logger.warning("openai SDK unavailable for judge")
            return None, "openai SDK not installed"
        client = _get_openai_client()
        if client is None:
            logger.warning("OPENAI_API_KEY missing for judge")
            return None, "OPENAI_API_KEY not configured"
        text_body, err = await _judge_openai(client, model, prompt, imgs, max_tokens)

    if err is not None or text_body is None:
        return None, err or "no response"
    raw = _extract_json_object(text_body)
    if raw is None:
        return None, "no JSON in LLM response"
    try:
        parsed = json.loads(raw)
    except Exception:
        return None, "JSON parse failed"
    if not isinstance(parsed, dict):
        return None, "JSON root is not an object"
    return parsed, None
