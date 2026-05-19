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
    # Anthropic SDK keys (must come before generic sk- pattern)
    (re.compile(r'\bsk-ant-[A-Za-z0-9_-]{20,}\b'), '[REDACTED-ANTHROPIC-KEY]', 'anthropic_key'),
    # Generic short-key prefixes (OpenAI, Stripe, etc.)
    (re.compile(r'\b(sk|rk|pk)[-_][A-Za-z0-9]{20,}\b'), '[REDACTED-API-KEY]', 'api_key'),
    (re.compile(r'\b[Bb]earer\s+[A-Za-z0-9._~+/=-]{16,}\b'), 'Bearer [REDACTED]', 'bearer_token'),
    (re.compile(r'\bAKIA[0-9A-Z]{16}\b'), '[REDACTED-AWS-KEY]', 'aws_access_key'),
    (re.compile(r'\bghp_[A-Za-z0-9]{30,}\b'), '[REDACTED-GITHUB-TOKEN]', 'github_pat'),
    (re.compile(r'\bgithub_pat_[A-Za-z0-9_]{30,}\b'), '[REDACTED-GITHUB-TOKEN]', 'github_pat'),
    # Slack tokens: xox{b,p,a,r,s,e}-... and the separate xapp-... app-level
    (re.compile(r'\b(?:xox[baprs]|xapp)-[A-Za-z0-9-]{10,}\b'), '[REDACTED-SLACK-TOKEN]', 'slack_token'),
    # Google API keys
    (re.compile(r'\bAIza[0-9A-Za-z_-]{35}\b'), '[REDACTED-GOOGLE-API-KEY]', 'google_api_key'),
    # GitLab personal access tokens
    (re.compile(r'\bglpat-[A-Za-z0-9_-]{20,}\b'), '[REDACTED-GITLAB-TOKEN]', 'gitlab_pat'),
    # SSH/PEM private keys
    (re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----'), '[REDACTED-PRIVATE-KEY]', 'private_key'),
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


def _redact_obj(obj: Any, tool_name: str) -> tuple[Any, bool]:
    """Recursively redact secrets from nested dicts/lists/strings."""
    if isinstance(obj, str):
        new_s, changed = _redact_secrets(obj, tool_name)
        if _SSN_RE.search(obj):
            new_s = _SSN_RE.sub('[REDACTED-SSN]', new_s)
            changed = True
        return new_s, changed
    if isinstance(obj, dict):
        new_d = {}
        any_changed = False
        for k, v in obj.items():
            new_v, changed = _redact_obj(v, tool_name)
            new_d[k] = new_v
            any_changed = any_changed or changed
        return new_d, any_changed
    if isinstance(obj, list):
        new_l = []
        any_changed = False
        for item in obj:
            new_item, changed = _redact_obj(item, tool_name)
            new_l.append(new_item)
            any_changed = any_changed or changed
        return new_l, any_changed
    return obj, False


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
        try:
            if input_data.get("hook_event_name") != "PreToolUse":
                return {}
            tool_name = input_data.get("tool_name", "")
            tool_input = input_data.get("tool_input", {})

            # Budget cap check — atomic reserve. Fails open (non-security) on
            # DB error but logs it. The atomic INSERT/UPDATE WHERE eliminates
            # the TOCTOU window between read-then-increment.
            try:
                from api.db.queries.budgets import try_consume_tool_call
                async with db_factory() as db:
                    result = await try_consume_tool_call(db, project_key)
                if result is not None:
                    used = result["used"]
                    cap = result["cap"]
                    if not result["ok"]:
                        logger.warning(
                            "budget_cap_exceeded project=%s tool=%s used=%d cap=%s",
                            project_key, tool_name, used, cap,
                        )
                        return {
                            "decision": "block",
                            "reason": f"Tool call budget exhausted ({used}/{cap} calls used this period).",
                        }
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

            # Recursively redact credentials + SSNs from all nested values
            redacted_input, changed = _redact_obj(tool_input, tool_name)
            if changed:
                return {"decision": "allow", "updatedInput": redacted_input}
            return {}
        except Exception:
            logger.error("pre_tool_use_hook_error tool=%s", input_data.get("tool_name", "?"), exc_info=True)
            return {}

    async def post_tool_use_hook(input_data: dict[str, Any]) -> dict[str, Any]:
        if input_data.get("hook_event_name") != "PostToolUse":
            return {}
        tool_name = input_data.get("tool_name", "")
        tool_output = str(input_data.get("tool_result", "") or "")

        # CAS fact-check
        try:
            async with db_factory() as db:
                result = await check_tool_output(tool_name, tool_output, db)
            if result["warnings"]:
                logger.warning("fact_id_check_warnings", extra={"tool": tool_name, "warnings": result["warnings"]})
        except Exception:
            logger.error("post_tool_use_fact_check_error tool=%s", tool_name, exc_info=True)

        # tool_calls spend is reserved atomically in PreToolUse via
        # try_consume_tool_call(); no PostToolUse increment is needed.
        # Tokens and experiments still ride the post-stream / explicit-call
        # paths (which don't have the same TOCTOU concern) and go through
        # increment_spend() at their own call sites.

        return {}

    return {
        "PreToolUse": [HookMatcher(hooks=[pre_tool_use_hook])],
        "PostToolUse": [HookMatcher(hooks=[post_tool_use_hook])],
    }
