"""P3 review-finding: `score_chunks_with_llm` control flow tests.

The function has several non-LLM branches that the existing
`test_paper_rcs_json.py` doesn't cover:
  - `chunks=[]` → returns `[]` early
  - `RCS_PROVIDER=anthropic` + no key → every chunk gets `rcs_error`
  - `RCS_PROVIDER=openai`  + no key → every chunk gets `rcs_error`
  - `RCS_PROVIDER=garbage` → warn + fall back to anthropic
  - SDK ImportError → unscored with marker
  - Score-clamping to [1, 10]
  - Summary truncation to 1500 chars
  - `_envelope` for the str-vs-dict return path

All cases use mocked provider clients — no real LLM calls.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest


def _chunks(n: int = 1) -> list[dict[str, Any]]:
    return [
        {
            "id": f"c{i}",
            "text": f"chunk text {i}",
            "title": "T",
            "doi": "10.x/y",
            "section": "Methods",
        }
        for i in range(n)
    ]


async def test_score_empty_chunks_returns_empty() -> None:
    from api.db.queries.paper_rcs import score_chunks_with_llm
    assert await score_chunks_with_llm([], query="anything") == []


async def test_anthropic_missing_key_marks_all_with_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default provider is anthropic. Without ANTHROPIC_API_KEY every
    chunk gets `rcs_error` and the original chunk fields survive."""
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("RCS_PROVIDER", "anthropic")
    # Force the cached client to None so the missing-key check fires.
    import api.db.queries.paper_rcs as paper_rcs
    monkeypatch.setattr(paper_rcs, "_anthropic_client", None)
    monkeypatch.setattr(paper_rcs, "_get_anthropic_client", lambda: None)

    from api.db.queries.paper_rcs import score_chunks_with_llm
    out = await score_chunks_with_llm(_chunks(3), query="q")
    assert len(out) == 3
    for c in out:
        assert "rcs_error" in c
        assert "ANTHROPIC_API_KEY" in c["rcs_error"]
        # Original fields preserved.
        assert c["title"] == "T"


async def test_openai_missing_key_marks_all_with_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """RCS_PROVIDER=openai + no OPENAI_API_KEY → fail-closed, no
    silent fallback to anthropic."""
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("RCS_PROVIDER", "openai")
    import api.db.queries.paper_rcs as paper_rcs
    monkeypatch.setattr(paper_rcs, "_openai_client", None)
    monkeypatch.setattr(paper_rcs, "_get_openai_client", lambda: None)

    from api.db.queries.paper_rcs import score_chunks_with_llm
    out = await score_chunks_with_llm(_chunks(2), query="q")
    assert len(out) == 2
    for c in out:
        assert "rcs_error" in c
        assert "OPENAI_API_KEY" in c["rcs_error"]


async def test_invalid_provider_falls_back_to_anthropic(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture,
) -> None:
    """RCS_PROVIDER=garbage → warn + default to anthropic. (Then
    anthropic also has no key, so all chunks get rcs_error — but the
    key bit is which provider was selected.)"""
    import logging
    monkeypatch.setenv("RCS_PROVIDER", "garbage")
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    import api.db.queries.paper_rcs as paper_rcs
    monkeypatch.setattr(paper_rcs, "_get_anthropic_client", lambda: None)

    from api.db.queries.paper_rcs import score_chunks_with_llm
    with caplog.at_level(logging.WARNING):
        out = await score_chunks_with_llm(_chunks(1), query="q")
    assert len(out) == 1
    assert any("invalid RCS_PROVIDER" in m for m in caplog.messages)
    # Fell back to anthropic — error message proves which provider was tried.
    assert "ANTHROPIC_API_KEY" in out[0]["rcs_error"]


async def test_score_clamps_outside_one_to_ten(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A model that returns score=15 or score=-3 must be clamped to
    the [1, 10] range. Mock the anthropic client to return values
    outside the range."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("RCS_PROVIDER", "anthropic")

    # The client.messages.create response shape: a list of blocks with
    # .type and .text. Return one that includes JSON with score=15.
    mock_block = MagicMock()
    mock_block.type = "text"
    mock_block.text = '```json\n{"score": 15, "summary": "Excellent"}\n```'
    mock_response = MagicMock()
    mock_response.content = [mock_block]

    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    import api.db.queries.paper_rcs as paper_rcs
    monkeypatch.setattr(paper_rcs, "_get_anthropic_client", lambda: mock_client)

    from api.db.queries.paper_rcs import score_chunks_with_llm
    out = await score_chunks_with_llm(_chunks(1), query="q")
    assert out[0]["relevance_score"] == 10  # clamped down from 15

    # Negative score → clamped UP to 1.
    mock_block.text = '```json\n{"score": -3, "summary": "Awful"}\n```'
    out = await score_chunks_with_llm(_chunks(1), query="q")
    assert out[0]["relevance_score"] == 1


async def test_summary_truncated_to_1500_chars(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM responses with long summaries must be capped at 1500 chars
    before they hit the DB / agent response."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("RCS_PROVIDER", "anthropic")
    long_summary = "x" * 5000
    import json
    payload = json.dumps({"score": 7, "summary": long_summary})
    mock_block = MagicMock()
    mock_block.type = "text"
    mock_block.text = payload  # raw JSON, no fence — _extract_json_object handles
    mock_response = MagicMock()
    mock_response.content = [mock_block]
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    import api.db.queries.paper_rcs as paper_rcs
    monkeypatch.setattr(paper_rcs, "_get_anthropic_client", lambda: mock_client)

    from api.db.queries.paper_rcs import score_chunks_with_llm
    out = await score_chunks_with_llm(_chunks(1), query="q")
    assert out[0]["relevance_score"] == 7
    assert len(out[0]["summary"]) <= 1500


async def test_missing_score_in_response_marks_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """LLM returns JSON without a `score` key (or non-numeric) →
    rcs_error: 'score missing or non-numeric'."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("RCS_PROVIDER", "anthropic")
    mock_block = MagicMock()
    mock_block.type = "text"
    mock_block.text = '{"summary": "but no score"}'
    mock_response = MagicMock()
    mock_response.content = [mock_block]
    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    import api.db.queries.paper_rcs as paper_rcs
    monkeypatch.setattr(paper_rcs, "_get_anthropic_client", lambda: mock_client)

    from api.db.queries.paper_rcs import score_chunks_with_llm
    out = await score_chunks_with_llm(_chunks(1), query="q")
    assert "rcs_error" in out[0]
    assert "score" in out[0]["rcs_error"]
    assert "relevance_score" not in out[0]


async def test_llm_call_failure_surfaces_per_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Network error / rate limit / anything else from
    client.messages.create → rcs_error per chunk, other chunks
    continue (asyncio.gather doesn't bail on one failure)."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    monkeypatch.setenv("RCS_PROVIDER", "anthropic")

    mock_client = MagicMock()
    mock_client.messages.create = AsyncMock(side_effect=RuntimeError("rate limit"))

    import api.db.queries.paper_rcs as paper_rcs
    monkeypatch.setattr(paper_rcs, "_get_anthropic_client", lambda: mock_client)

    from api.db.queries.paper_rcs import score_chunks_with_llm
    out = await score_chunks_with_llm(_chunks(3), query="q")
    assert len(out) == 3
    for c in out:
        assert "rcs_error" in c
        assert "LLM call failed" in c["rcs_error"]
