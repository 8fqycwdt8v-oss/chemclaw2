"""Tests for `api.agent.hooks` credential and PII redaction.

These are pure-unit tests — no DB, no network. They lock in the
contract of `_SECRET_PATTERNS`, `_redact_secrets`, `_redact_obj`,
and the SSN pattern, plus a guard that the secret itself never
appears in the log record (`caplog`).
"""
from __future__ import annotations

import logging

import pytest

from api.agent.hooks import _redact_obj, _redact_secrets


# Real-world-shaped tokens. Each is long enough to satisfy the
# pattern's minimum length but doesn't correspond to a real account.
_ANTHROPIC = "sk-ant-api03-" + "A" * 30
_OPENAI = "sk-" + "B" * 40
# Generic pk-prefixed key with only one separator (matches the generic
# (sk|rk|pk)[-_][A-Za-z0-9]{20,} pattern).
_PUBLIC = "pk_" + "C" * 24
# Stripe: dedicated pattern handles the second underscore.
_STRIPE_LIVE_SK = "sk_live_" + "S" * 24
_STRIPE_TEST_PK = "pk_test_" + "T" * 24
_BEARER = "Bearer " + "D" * 32
_JWT = "eyJ" + "h" * 18 + ".eyJ" + "p" * 18 + "." + "s" * 22
_AWS = "AKIAIOSFODNN7EXAMPLE"
_GHP = "ghp_" + "E" * 36
_GITHUB_PAT = "github_pat_" + "F" * 35
_SLACK_BOT = "xoxb-1234567890-1234567890-AbCdEfGh"
_SLACK_USER = "xoxp-1-abcdefghijklmnop"
_SLACK_APP = "xapp-1-A1B2C3D4-1234567890"
_GOOGLE = "AIza" + "G" * 35
_GITLAB = "glpat-aBcDe1234567890_-xyz0"
_SENDGRID = "SG." + "A" * 22 + "." + "B" * 43
_TWILIO = "AC" + "0123456789abcdef0123456789abcdef"
_NPM = "npm_" + "n" * 36
_PEM = "-----BEGIN RSA PRIVATE KEY-----"
_SSN = "123-45-6789"


@pytest.mark.parametrize(
    "raw,tag",
    [
        (_ANTHROPIC, "ANTHROPIC"),
        (_OPENAI, "API-KEY"),
        (_PUBLIC, "API-KEY"),
        (_STRIPE_LIVE_SK, "STRIPE"),
        (_STRIPE_TEST_PK, "STRIPE"),
        (_BEARER, "REDACTED"),
        (_JWT, "JWT"),
        (_AWS, "AWS"),
        (_GHP, "GITHUB"),
        (_GITHUB_PAT, "GITHUB"),
        (_SLACK_BOT, "SLACK"),
        (_SLACK_USER, "SLACK"),
        (_SLACK_APP, "SLACK"),
        (_GOOGLE, "GOOGLE"),
        (_GITLAB, "GITLAB"),
        (_SENDGRID, "SENDGRID"),
        (_TWILIO, "TWILIO"),
        (_NPM, "NPM"),
        (_PEM, "PRIVATE-KEY"),
    ],
)
def test_each_pattern_redacts(raw: str, tag: str) -> None:
    out, changed = _redact_secrets(raw, "test")
    assert changed, f"pattern did not match: {raw!r}"
    assert raw not in out, f"raw secret still present in output: {out!r}"
    assert tag in out, f"expected tag {tag!r} in {out!r}"


def test_anthropic_precedence_over_generic_sk() -> None:
    """sk-ant- must match before the generic sk- pattern so the redaction
    is tagged ANTHROPIC, not the generic API-KEY tag."""
    out, _ = _redact_secrets(_ANTHROPIC, "test")
    assert "ANTHROPIC" in out
    assert "[REDACTED-API-KEY]" not in out


def test_stripe_precedence_over_generic_pk_sk() -> None:
    """Stripe pattern must match before the generic sk/pk fallback, otherwise
    `sk_live_…` would either fail (second underscore stops the generic regex)
    or be tagged as a plain API-KEY rather than Stripe-specific."""
    out, _ = _redact_secrets(_STRIPE_LIVE_SK, "test")
    assert "STRIPE" in out
    assert "[REDACTED-API-KEY]" not in out


def test_ssn_redacted_via_redact_obj() -> None:
    out, changed = _redact_obj(f"contact at {_SSN} today", "test")
    assert changed
    assert "[REDACTED-SSN]" in out
    assert _SSN not in out


def test_recursion_through_nested_dict_and_list() -> None:
    nested = {
        "name": "ok",
        "creds": [_ANTHROPIC, {"key": _GOOGLE, "note": "nothing here"}],
        "deep": {"a": {"b": {"c": _SLACK_BOT}}},
    }
    out, changed = _redact_obj(nested, "test")
    assert changed
    flat = repr(out)
    for raw in (_ANTHROPIC, _GOOGLE, _SLACK_BOT):
        assert raw not in flat, f"{raw!r} leaked through recursion: {flat}"
    # Plain values must be preserved
    assert out["name"] == "ok"
    assert out["creds"][1]["note"] == "nothing here"


def test_non_string_values_pass_through_unchanged() -> None:
    obj = {"n": 42, "f": 3.14, "b": True, "z": None}
    out, changed = _redact_obj(obj, "test")
    assert not changed
    assert out == obj


def test_redaction_does_not_log_the_secret(caplog: pytest.LogCaptureFixture) -> None:
    """When a pattern matches, the WARNING log must record kind/count but
    must NOT include the matched secret itself. A leaked secret in the
    log would be the very thing the redactor is meant to prevent."""
    caplog.set_level(logging.WARNING, logger="api.agent.hooks")
    secret = _ANTHROPIC
    _redact_secrets(f"prefix {secret} suffix", "test_tool")
    for record in caplog.records:
        msg = record.getMessage()
        # The structured extras may serialise via record.__dict__; check the
        # full record dict for accidental leakage too.
        full = msg + " " + repr(record.__dict__)
        assert secret not in full, f"secret leaked into log record: {full[:300]}"
    # And the redaction event must have fired.
    assert any("credential_redacted" in r.getMessage() for r in caplog.records)


def test_no_match_returns_input_unchanged() -> None:
    s = "plain text with nothing sensitive: SMILES=CCO"
    out, changed = _redact_secrets(s, "test")
    assert out == s
    assert changed is False


def test_extract_string_values_depth_capped() -> None:
    """_extract_string_values caps recursion at depth=10 to defend against
    pathologically nested tool inputs."""
    from api.agent.hooks import _extract_string_values

    # Build a structure that nests 15 levels deep.
    obj: object = "leaf"
    for _ in range(15):
        obj = {"x": obj}
    found = _extract_string_values(obj)
    # Depth cap is 10; the leaf at depth 15 should be unreachable.
    assert "leaf" not in found
