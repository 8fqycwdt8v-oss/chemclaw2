"""Tests for the agent confidence extractor + SSE emission.

`_extract_confidence` is a pure regex helper — pin its behaviour
(strip, case, multi-tag, malformed) without standing up the SDK.
The streaming integration is covered indirectly by the existing
substance gate tests; here we just test the parser.
"""
from __future__ import annotations

import pytest

from api.agent.runner import _extract_confidence


@pytest.mark.parametrize(
    "text,expected_level",
    [
        ("Some answer.\n<confidence>high</confidence>", "high"),
        ("Some answer.\n<confidence>med</confidence>", "med"),
        ("Some answer.\n<confidence>low</confidence>", "low"),
        # Case-insensitive
        ("Body.\n<CONFIDENCE>HIGH</CONFIDENCE>", "high"),
        ("Body.\n<Confidence>Med</Confidence>", "med"),
        # Leading/trailing whitespace
        ("Body.\n   <confidence>  low  </confidence>   ", "low"),
        # No tag at all
        ("Just plain text without any tag.", None),
        # Malformed level — caller falls back to None rather than guessing
        ("Body.<confidence>maybe</confidence>", None),
        ("Body.<confidence></confidence>", None),
    ],
)
def test_extract_confidence_parses(text: str, expected_level: str | None) -> None:
    _, level = _extract_confidence(text)
    assert level == expected_level


def test_extract_confidence_strips_tag_from_text() -> None:
    """The cleaned text must not contain the marker — the runner uses the
    cleaned form for the SSE `text` event."""
    text = "The answer is 42.\n<confidence>high</confidence>"
    cleaned, level = _extract_confidence(text)
    assert level == "high"
    assert "<confidence>" not in cleaned
    assert "</confidence>" not in cleaned
    assert "high" not in cleaned.lower() or "high" in "The answer is 42."


def test_extract_confidence_takes_last_when_multiple() -> None:
    """A streamed response can accumulate intermediate confidence tags
    (e.g. the agent revised mid-answer). Only the last one matters."""
    text = (
        "First pass: <confidence>low</confidence> Wait, found a source.\n"
        "<confidence>high</confidence>"
    )
    cleaned, level = _extract_confidence(text)
    assert level == "high"
    # Both tags stripped.
    assert "<confidence>" not in cleaned


def test_extract_confidence_preserves_unrelated_xml() -> None:
    """Only the literal <confidence>...</confidence> tag is touched — other
    XML-ish markup (e.g. citation markers) survives intact."""
    text = "See [<citation:abc>], also <other>foo</other>.\n<confidence>med</confidence>"
    cleaned, level = _extract_confidence(text)
    assert level == "med"
    assert "[<citation:abc>]" in cleaned
    assert "<other>foo</other>" in cleaned


def test_extract_confidence_returns_text_unchanged_when_absent() -> None:
    """No tag → cleaned text equals input verbatim (no spurious strip)."""
    text = "Body without any confidence marker."
    cleaned, level = _extract_confidence(text)
    assert cleaned == text
    assert level is None


def test_extract_confidence_empty_input() -> None:
    cleaned, level = _extract_confidence("")
    assert cleaned == ""
    assert level is None
