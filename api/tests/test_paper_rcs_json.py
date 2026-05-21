"""Tests for the PaperQA2-style RCS JSON extraction.

The LLM response parser was changed from a fragile non-greedy regex to a
balanced-brace scanner that also tries a ```json fenced block first.
These tests pin the new behaviour against the inputs that were known to
break the old version.
"""
from __future__ import annotations

from api.db.queries.paper_rcs import _extract_json_object


def test_extract_plain_object() -> None:
    raw = '{"score": 8, "summary": "Direct hit."}'
    assert _extract_json_object(raw) == raw


def test_extract_with_surrounding_prose() -> None:
    raw = 'Here is my assessment: {"score": 7, "summary": "Partial."}\n\nLet me know.'
    out = _extract_json_object(raw)
    assert out == '{"score": 7, "summary": "Partial."}'


def test_extract_handles_braces_inside_summary() -> None:
    """The old non-greedy regex `\\{[\\s\\S]*?\\}` truncated at the first `}`,
    losing the closing brace of the outer object when the summary contained
    literal braces. The balanced-brace scanner must walk past them."""
    raw = '{"score": 9, "summary": "the LaTeX form {x} matters"}'
    out = _extract_json_object(raw)
    assert out == raw


def test_extract_prefers_fenced_block() -> None:
    raw = """Here is the answer.

```json
{"score": 6, "summary": "Tangential."}
```

Anything else?"""
    out = _extract_json_object(raw)
    assert out == '{"score": 6, "summary": "Tangential."}'


def test_extract_handles_string_with_escaped_quotes() -> None:
    """The depth counter must ignore quotes inside strings but respect
    `\\"` escapes."""
    raw = '{"score": 5, "summary": "she said \\"yes\\" but then {context}"}'
    out = _extract_json_object(raw)
    assert out == raw


def test_extract_returns_none_on_no_brace() -> None:
    assert _extract_json_object("no JSON here at all") is None
    assert _extract_json_object("") is None


def test_extract_returns_none_on_unbalanced() -> None:
    """Unclosed object — the scanner should return None rather than a partial
    string (which would then fail json.loads downstream, but with a
    misleading 'JSON parse failed' rcs_error instead of 'no JSON in LLM
    response')."""
    assert _extract_json_object('{"score": 7, "summary": "no close') is None


def test_extract_handles_nested_objects() -> None:
    raw = '{"score": 8, "summary": "x", "meta": {"k": "v"}}'
    out = _extract_json_object(raw)
    assert out == raw


def test_extract_picks_first_object_when_multiple() -> None:
    raw = '{"score": 7, "summary": "A"} then {"score": 2, "summary": "B"}'
    out = _extract_json_object(raw)
    assert out == '{"score": 7, "summary": "A"}'
