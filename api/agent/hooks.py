"""Agent security hooks — Python port of packages/agent-tools/src/hooks/*.ts."""
from __future__ import annotations

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

def _normalize(s: str) -> str:
    normalized = unicodedata.normalize('NFKC', s)
    # Strip every Unicode format character (category Cf): zero-width
    # space/joiner/non-joiner, BOM, soft hyphen, bidi marks (LRM/RLM/ALM),
    # and invisible math operators. Any of these can be interspersed inside
    # a substance name to defeat the \b...\b regex, so an explicit small
    # char-class (the previous approach) is not enough.
    return ''.join(ch for ch in normalized if unicodedata.category(ch) != 'Cf')


def scheduled_substance_gate(prompt: str) -> dict[str, Any]:
    normalized = _normalize(prompt)
    matched = bool(_CONTROLLED_SUBSTANCES.search(normalized)) and bool(_SYNTHESIS_VERBS.search(normalized))
    if matched:
        logger.error("scheduled_substance_attempt", extra={"prompt_len": len(prompt)})
        # Lazy import keeps prometheus_client off the hot path for callers
        # that import this module before the observability package is wired.
        from api.observability.metrics import substance_gate_blocked_total
        substance_gate_blocked_total.inc()
        return {
            "blocked": True,
            "matched": True,
            "reason": "Request blocked: synthesis instructions for scheduled/controlled substances are not permitted.",
        }
    return {"blocked": False, "matched": False}


# ── Credential redaction ──────────────────────────────────────────────────────

_SSN_RE = re.compile(r'\b\d{3}-\d{2}-\d{4}\b')

_SECRET_PATTERNS: list[tuple[re.Pattern[str], str, str]] = [
    # Anthropic SDK keys (must come before any generic sk- pattern)
    (re.compile(r'\bsk-ant-[A-Za-z0-9_-]{20,}\b'), '[REDACTED-ANTHROPIC-KEY]', 'anthropic_key'),
    # Stripe live/test keys: must come before the generic sk/pk fallback
    # because Stripe's `sk_live_<rest>` has a second underscore that the
    # `[-_]` separator in the generic pattern stops on.
    (re.compile(r'\b(?:sk|rk|pk)_(?:live|test)_[A-Za-z0-9]{16,}\b'), '[REDACTED-STRIPE-KEY]', 'stripe_key'),
    # Generic short-key prefixes (OpenAI restricted/public, custom providers).
    # The charset includes internal `-`/`_` so multi-segment keys are caught:
    # current OpenAI keys are `sk-proj-…` / `sk-svcacct-…`, where only 4 chars
    # ("proj") precede the next hyphen — a `[A-Za-z0-9]{20,}` body stops at that
    # hyphen and the whole live key sails through unredacted.
    (re.compile(r'\b(sk|rk|pk)[-_][A-Za-z0-9][A-Za-z0-9_-]{18,}\b'), '[REDACTED-API-KEY]', 'api_key'),
    (re.compile(r'\b[Bb]earer\s+[A-Za-z0-9._~+/=-]{16,}\b'), 'Bearer [REDACTED]', 'bearer_token'),
    # JWT: three base64url segments separated by dots. Anchor on the eyJ
    # prefix (base64-encoded `{"`) — every real JWT header starts with it.
    (re.compile(r'\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b'), '[REDACTED-JWT]', 'jwt'),
    (re.compile(r'\bAKIA[0-9A-Z]{16}\b'), '[REDACTED-AWS-KEY]', 'aws_access_key'),
    (re.compile(r'\bghp_[A-Za-z0-9]{30,}\b'), '[REDACTED-GITHUB-TOKEN]', 'github_pat'),
    (re.compile(r'\bgithub_pat_[A-Za-z0-9_]{30,}\b'), '[REDACTED-GITHUB-TOKEN]', 'github_pat'),
    # Slack tokens: xox{b,p,a,r,s,e}-... and the separate xapp-... app-level
    (re.compile(r'\b(?:xox[baprs]|xapp)-[A-Za-z0-9-]{10,}\b'), '[REDACTED-SLACK-TOKEN]', 'slack_token'),
    # Google API keys
    (re.compile(r'\bAIza[0-9A-Za-z_-]{35}\b'), '[REDACTED-GOOGLE-API-KEY]', 'google_api_key'),
    # GitLab personal access tokens
    (re.compile(r'\bglpat-[A-Za-z0-9_-]{20,}\b'), '[REDACTED-GITLAB-TOKEN]', 'gitlab_pat'),
    # SendGrid API keys (SG.<id>.<sig> shape, both segments base64url)
    (re.compile(r'\bSG\.[A-Za-z0-9_-]{22}\.[A-Za-z0-9_-]{43}\b'), '[REDACTED-SENDGRID-KEY]', 'sendgrid_key'),
    # Twilio account SIDs (companion auth tokens are unprefixed, so we
    # match the SID — its presence often indicates the auth token is nearby).
    (re.compile(r'\bAC[0-9a-fA-F]{32}\b'), '[REDACTED-TWILIO-SID]', 'twilio_sid'),
    # npm access tokens
    (re.compile(r'\bnpm_[A-Za-z0-9]{36}\b'), '[REDACTED-NPM-TOKEN]', 'npm_token'),
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


_REDACT_MAX_DEPTH = 12


def _redact_obj(obj: Any, tool_name: str, depth: int = 0) -> tuple[Any, bool]:
    """Recursively redact secrets from nested dicts/lists/tuples/strings.

    Bounded at `_REDACT_MAX_DEPTH` so a pathologically deep (or cyclic)
    tool_input can't drive a RecursionError — which the caller would catch
    and fail open, letting the raw input through. Redacts dict *keys* as well
    as values, and traverses tuples/sets, because a secret can appear in any
    position the SDK hands us.
    """
    if depth > _REDACT_MAX_DEPTH:
        return obj, False
    if isinstance(obj, str):
        new_s, changed = _redact_secrets(obj, tool_name)
        if _SSN_RE.search(new_s):
            new_s = _SSN_RE.sub('[REDACTED-SSN]', new_s)
            changed = True
        return new_s, changed
    if isinstance(obj, dict):
        new_d = {}
        any_changed = False
        for k, v in obj.items():
            new_k, k_changed = _redact_obj(k, tool_name, depth + 1)
            new_v, v_changed = _redact_obj(v, tool_name, depth + 1)
            new_d[new_k] = new_v
            any_changed = any_changed or k_changed or v_changed
        return new_d, any_changed
    if isinstance(obj, (list, tuple, set, frozenset)):
        any_changed = False
        items = []
        for item in obj:
            new_item, changed = _redact_obj(item, tool_name, depth + 1)
            items.append(new_item)
            any_changed = any_changed or changed
        return type(obj)(items), any_changed
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
        # Fail-open paths must log at ERROR (CLAUDE.md observability rule 5)
        # so a persistent DB outage silently disabling the fact-check is
        # visible in alerting, not buried at warning.
        logger.error(
            "cas_fact_check_db_error_fail_open",
            extra={"tool": tool_name, "cas_count": len(cas_numbers)},
            exc_info=True,
        )
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
                    return {
                        "decision": "block",
                        "reason": (
                            "Tool input blocked: contains a term that is not "
                            "permitted in this context."
                        ),
                    }

            # Recursively redact credentials + SSNs from all nested values
            redacted_input, changed = _redact_obj(tool_input, tool_name)
            if changed:
                return {"decision": "allow", "updatedInput": redacted_input}
            return {}
        except Exception:
            # The controlled-substance block and the credential redaction above
            # are security gates — if either raises we must fail CLOSED (block
            # the call), not return {} (allow with the original, unredacted
            # input). The budget check has its own inner try/except and is the
            # only sanctioned fail-open path.
            logger.error("pre_tool_use_hook_error tool=%s", input_data.get("tool_name", "?"), exc_info=True)
            return {
                "decision": "block",
                "reason": "Tool call blocked: input could not be safety-screened.",
            }

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
