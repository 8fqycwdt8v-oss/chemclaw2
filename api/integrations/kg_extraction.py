"""LLM extraction of knowledge-graph entries from a document.

`extract_world_model` asks Claude to pull atomic, verifiable facts/evidence
statements (each with a confidence) and a few worth-investigating hypotheses out
of a document, for the structured world model (`world_model_entries`) and the
hypothesis tournament (`hypotheses`). This is the "generate knowledge" step the
ingest pipeline runs in addition to the searchable chunks + wiki draft.

Best-effort and mirrors `document_enrichment.extract_entities_from_text`: the
anthropic SDK is imported lazily, the call is time-bounded, and any failure
returns an empty result with an `error` key rather than raising — ingestion must
never fail because knowledge extraction did.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

# Haiku by default — same low-difficulty structured-output workload as entity
# extraction. Reuses the ENTITY_EXTRACTION_MODEL override so operators tune one
# knob for both document LLM passes.
_DEFAULT_KG_MODEL = "claude-haiku-4-5-20251001"

# Only the kinds that make sense for document-derived knowledge. `assumption`
# and `open_question` are agent-interaction kinds, not things a source states.
_VALID_FACT_KINDS = {"fact", "evidence"}

_KG_SYSTEM_PROMPT = """\
You extract knowledge-graph entries from a document excerpt for a pharma R&D \
knowledge base. Be conservative — only emit what the text explicitly states or \
directly implies, and never invent numbers, yields, or conditions.

Facts: atomic, self-contained statements the document asserts. Use kind \
"evidence" for an experimental observation/result the document reports, and \
"fact" for established background knowledge it states. Give each a confidence \
in [0,1] reflecting how strongly the text supports it, and a one-sentence \
context snippet from the source.

Hypotheses: at most 3 testable claims worth investigating that the document \
motivates but does not itself settle. Give a short rationale grounded in the \
text. Omit hypotheses entirely if the document doesn't motivate any."""

_KG_TOOL_SCHEMA: dict[str, Any] = {
    "name": "extract_world_model",
    "description": "Emit the structured knowledge-graph entries for this document.",
    "input_schema": {
        "type": "object",
        "properties": {
            "facts": {
                "type": "array",
                "maxItems": 25,
                "items": {
                    "type": "object",
                    "properties": {
                        "content": {"type": "string", "maxLength": 600},
                        "kind": {"type": "string", "enum": ["fact", "evidence"]},
                        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                        "context": {"type": "string", "maxLength": 400},
                    },
                    "required": ["content", "kind", "confidence"],
                },
            },
            "hypotheses": {
                "type": "array",
                "maxItems": 3,
                "items": {
                    "type": "object",
                    "properties": {
                        "statement": {"type": "string", "maxLength": 400},
                        "rationale": {"type": "string", "maxLength": 600},
                    },
                    "required": ["statement"],
                },
            },
        },
        "required": ["facts", "hypotheses"],
    },
}


def _empty(error: str | None = None) -> dict[str, Any]:
    out: dict[str, Any] = {"facts": [], "hypotheses": []}
    if error is not None:
        out["error"] = error
    return out


async def extract_world_model(
    text: str,
    *,
    max_chars: int = 40000,
    timeout_sec: float = 30.0,
) -> dict[str, Any]:
    """Ask Claude to pull facts/evidence + hypotheses out of a document excerpt.

    Returns ``{"facts": [...], "hypotheses": [...]}`` (with an ``"error"`` key on
    failure). Never raises — the ingest path treats this as best-effort.
    """
    if not text or not text.strip():
        return _empty()

    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        logger.warning("kg_extraction_anthropic_sdk_missing")
        return _empty("anthropic SDK missing")

    snippet = text[:max_chars]
    model = os.environ.get("ENTITY_EXTRACTION_MODEL") or _DEFAULT_KG_MODEL
    client = AsyncAnthropic()

    try:
        response = await asyncio.wait_for(
            client.messages.create(  # type: ignore[call-overload]
                model=model,
                max_tokens=3000,
                system=_KG_SYSTEM_PROMPT,
                tools=[_KG_TOOL_SCHEMA],
                tool_choice={"type": "tool", "name": "extract_world_model"},
                messages=[{"role": "user", "content": snippet}],
            ),
            timeout=timeout_sec,
        )
    except TimeoutError:
        logger.warning("kg_extraction_timed_out chars=%d", len(snippet))
        return _empty("timeout")
    except Exception:
        logger.exception("kg_extraction_failed chars=%d", len(snippet))
        return _empty("kg extraction failed")

    for block in response.content:
        if getattr(block, "type", None) == "tool_use" and getattr(block, "name", None) == "extract_world_model":
            tool_input = getattr(block, "input", {}) or {}
            if isinstance(tool_input, dict):
                return {
                    "facts": _clean_facts(tool_input.get("facts") or []),
                    "hypotheses": tool_input.get("hypotheses") or [],
                }
    logger.warning("kg_extraction_no_tool_block model=%s", model)
    return _empty("no tool block")


def _clean_facts(facts: list[Any]) -> list[dict[str, Any]]:
    """Keep only well-formed fact dicts with a valid kind and content."""
    cleaned: list[dict[str, Any]] = []
    for f in facts:
        if not isinstance(f, dict):
            continue
        content = (f.get("content") or "").strip()
        kind = f.get("kind")
        if not content or kind not in _VALID_FACT_KINDS:
            continue
        cleaned.append(f)
    return cleaned
