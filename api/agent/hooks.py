"""Agent security hooks — Python port of packages/agent-tools/src/hooks/*.ts."""
from __future__ import annotations

import hashlib
import logging
import re
import unicodedata
from typing import Any

logger = logging.getLogger(__name__)

# ── Scheduled substance gate ──────────────────────────────────────────────────

_CONTROLLED_SUBSTANCES = re.compile(
    r'\b(fentanyl|carfentanil|acetylfentanyl|furanylfentanyl|nitazene|metonitazene|'
    r'isotonitazene|protonitazene|methamphetamine|meth|amphetamine|heroin|mdma|ecstasy|'
    r'molly|cocaine|crack|lsd|psilocybin|psilocin|dmt|mescaline|peyote|ghb|pcp|'
    r'phencyclidine|ketamine|oxycodone|hydrocodone|hydromorphone|oxymorphone|morphine|'
    r'codeine|buprenorphine|tramadol|tapentadol|mdpv|cathinone|mephedrone|ephedrine|'
    r'pseudoephedrine|safrole)\b',
    re.IGNORECASE,
)

_SYNTHESIS_VERBS = re.compile(
    r'\b(synthesize|synthesise|synthesis|manufacture|produce|make|cook|prepare|recipe|route)\b',
    re.IGNORECASE,
)

_ZERO_WIDTH = re.compile(r'[­​-‍⁠﻿]')


def _normalize(s: str) -> str:
    return _ZERO_WIDTH.sub('', unicodedata.normalize('NFKC', s))


def scheduled_substance_gate(prompt: str) -> dict[str, Any]:
    normalized = _normalize(prompt)
    matched = bool(_CONTROLLED_SUBSTANCES.search(normalized)) and bool(_SYNTHESIS_VERBS.search(normalized))
    if matched:
        logger.error("scheduled_substance_attempt", extra={"prompt_len": len(prompt)})
        return {
            "blocked": True,
            "matched": True,
            "reason": "Request blocked: synthesis instructions for scheduled/controlled substances are not permitted.",
        }
    return {"blocked": False, "matched": False}


# ── Credential redaction ──────────────────────────────────────────────────────

_SSN_RE = re.compile(r'\b\d{3}-\d{2}-\d{4}\b')

_SECRET_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(r'\b(sk|rk|pk)[-_][A-Za-z0-9]{20,}\b'), '[REDACTED-API-KEY]', 'api_key'),
    (re.compile(r'\b[Bb]earer\s+[A-Za-z0-9._~+/=-]{16,}\b'), 'Bearer [REDACTED]', 'bearer_token'),
    (re.compile(r'\bAKIA[0-9A-Z]{16}\b'), '[REDACTED-AWS-KEY]', 'aws_access_key'),
    (re.compile(r'\bghp_[A-Za-z0-9]{30,}\b'), '[REDACTED-GITHUB-TOKEN]', 'github_pat'),
    (re.compile(r'\bgithub_pat_[A-Za-z0-9_]{30,}\b'), '[REDACTED-GITHUB-TOKEN]', 'github_pat'),
]


def _redact_secrets(s: str, tool_name: str) -> tuple[str, bool]:
    matched = False
    out = s
    for pattern, sub, kind in _SECRET_PATTERNS:
        count = len(pattern.findall(out))
        if count:
            logger.warning("credential_redacted", extra={"tool": tool_name, "kind": kind, "count": count})
            out = pattern.sub(sub, out)
            matched = True
    return out, matched


def _extract_string_values(obj: Any, depth: int = 0) -> list[str]:
    if depth > 10:
        return []
    if isinstance(obj, str):
        return [obj]
    if isinstance(obj, dict):
        return [v for val in obj.values() for v in _extract_string_values(val, depth + 1)]
    if isinstance(obj, list):
        return [v for item in obj for v in _extract_string_values(item, depth + 1)]
    return []


# ── CAS number fact-check ─────────────────────────────────────────────────────

# Prefix 2–7 digits covers all real-world CAS RNs as of 2025 (the largest
# registered number has a 7-digit prefix). TypeScript used \d{2,10} which
# over-matches non-CAS numeric strings; Python narrows to \d{2,7}.
_CAS_RE = re.compile(r'\b\d{2,7}-\d{2}-\d\b')

async def check_tool_output(tool_name: str, tool_output: str, db: Any) -> dict[str, list[str]]:
    """Check tool output for unregistered CAS numbers.

    Fails open (returns no warnings) on DB error — this is a fact-check guard,
    not a security gate. The error is always logged so DB outages are visible.
    """
    from api.db.queries.compounds import known_cas_numbers

    cas_numbers = list(set(_CAS_RE.findall(tool_output)))
    if not cas_numbers:
        return {"warnings": []}

    try:
        known = await known_cas_numbers(db, cas_numbers)
    except Exception:
        logger.warning("fact_id_check_db_error", extra={"tool": tool_name, "cas_count": len(cas_numbers)}, exc_info=True)
        return {"warnings": ["CAS fact-check unavailable — database error"]}

    unknown = [c for c in cas_numbers if c not in known]
    if unknown:
        logger.warning("fact_id_check_unknown_cas", extra={"tool": tool_name, "unknown": unknown})
        return {"warnings": [f"Unregistered CAS number in tool output: {c}" for c in unknown]}
    return {"warnings": []}


# ── Hook builder ──────────────────────────────────────────────────────────────

def build_hooks(user_id: str, project_key: str, db_factory: Any) -> dict[str, list[Any]]:
    """Build the hooks dict for ClaudeAgentOptions.

    Returns a dict matching the SDK's hooks type:
    {event_name: [HookMatcher, ...]}
    """
    from claude_agent_sdk.types import HookMatcher

    async def pre_tool_use_hook(input_data: dict[str, Any]) -> dict[str, Any]:
        if input_data.get("hook_event_name") != "PreToolUse":
            return {}
        tool_name = input_data.get("tool_name", "")
        tool_input = input_data.get("tool_input", {})

        # Budget cap check — fail open (non-security) but log on error.
        try:
            from api.db.queries.budgets import get_budget_with_spend
            async with db_factory() as db:
                budget_info = await get_budget_with_spend(db, project_key)
            # get_budget_with_spend returns a flat dict: cap fields at top level,
            # spend sub-dict under "spend" key with column names tool_calls/experiments/tokens.
            if budget_info:
                spend = budget_info.get("spend") or {}
                cap = budget_info.get("tool_calls_cap")
                used = spend.get("tool_calls", 0) or 0
                if cap is not None and used >= cap:
                    logger.warning(
                        "budget_cap_exceeded project=%s tool=%s used=%d cap=%d",
                        project_key, tool_name, used, cap,
                    )
                    return {"decision": "block", "reason": f"Tool call budget exhausted ({used}/{cap} calls used this period)."}
                # Warn when within 10% of cap.
                if cap is not None and cap > 0 and (used / cap) >= 0.9:
                    logger.warning(
                        "budget_cap_near project=%s tool=%s used=%d cap=%d",
                        project_key, tool_name, used, cap,
                    )
        except Exception:
            logger.error("budget_check_failed project=%s tool=%s", project_key, tool_name, exc_info=True)

        # Block controlled substance names in tool params
        for val in _extract_string_values(tool_input):
            if _CONTROLLED_SUBSTANCES.search(_normalize(val)):
                logger.warning("tool_input_block_controlled_substance", extra={"tool": tool_name})
                return {"decision": "block", "reason": "Tool input blocked: contains a term that is not permitted in this context."}

        # Redact credentials + SSNs from string values
        redacted_input = dict(tool_input)
        changed = False
        for key, val in list(redacted_input.items()):
            if isinstance(val, str):
                new_val, did_redact = _redact_secrets(val, tool_name)
                if _SSN_RE.search(val):
                    new_val = _SSN_RE.sub('[REDACTED-SSN]', new_val)
                    did_redact = True
                if did_redact:
                    redacted_input[key] = new_val
                    changed = True
        if changed:
            return {"decision": "allow", "updatedInput": redacted_input}
        return {}

    async def post_tool_use_hook(input_data: dict[str, Any]) -> dict[str, Any]:
        if input_data.get("hook_event_name") != "PostToolUse":
            return {}
        tool_name = input_data.get("tool_name", "")
        tool_output = str(input_data.get("tool_result", "") or "")

        async with db_factory() as db:
            result = await check_tool_output(tool_name, tool_output, db)
        if result["warnings"]:
            logger.warning("fact_id_check_warnings", extra={"tool": tool_name, "warnings": result["warnings"]})
        return {}

    return {
        "PreToolUse": [HookMatcher(hooks=[pre_tool_use_hook])],
        "PostToolUse": [HookMatcher(hooks=[post_tool_use_hook])],
    }
