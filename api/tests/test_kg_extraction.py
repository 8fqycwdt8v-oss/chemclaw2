"""Unit tests for the KG-extraction LLM wrapper (no network).

Injects a fake `anthropic.AsyncAnthropic` whose `messages.create` returns a
canned tool_use block, and checks parsing, fact cleaning, and the best-effort
empty-result paths.
"""
from __future__ import annotations

import sys
import types
from typing import Any

import pytest

from api.integrations import kg_extraction as kg


class _Block:
    def __init__(self, type_: str, name: str, input_: dict[str, Any]) -> None:
        self.type = type_
        self.name = name
        self.input = input_


class _Resp:
    def __init__(self, content: list[Any]) -> None:
        self.content = content


def _install_fake_anthropic(monkeypatch: pytest.MonkeyPatch, response: Any) -> None:
    class _Messages:
        async def create(self, **kwargs: Any) -> Any:
            if isinstance(response, Exception):
                raise response
            return response

    class _FakeClient:
        def __init__(self, *a: Any, **k: Any) -> None:
            self.messages = _Messages()

    fake = types.ModuleType("anthropic")
    fake.AsyncAnthropic = _FakeClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "anthropic", fake)


@pytest.mark.asyncio
async def test_empty_text_short_circuits() -> None:
    assert await kg.extract_world_model("   ") == {"facts": [], "hypotheses": []}


@pytest.mark.asyncio
async def test_parses_tool_block(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = _Resp([
        _Block(
            "tool_use",
            "extract_world_model",
            {
                "facts": [
                    {"content": "Pd/C reduces alkenes.", "kind": "fact", "confidence": 0.9},
                    {"content": "Yield was 92%.", "kind": "evidence", "confidence": 0.8, "context": "Table 1"},
                ],
                "hypotheses": [
                    {"statement": "Pt/C gives higher selectivity.", "rationale": "Analogy."},
                ],
            },
        )
    ])
    _install_fake_anthropic(monkeypatch, resp)
    out = await kg.extract_world_model("some chemistry text")
    assert len(out["facts"]) == 2
    assert out["facts"][1]["kind"] == "evidence"
    assert len(out["hypotheses"]) == 1


@pytest.mark.asyncio
async def test_drops_malformed_facts(monkeypatch: pytest.MonkeyPatch) -> None:
    resp = _Resp([
        _Block(
            "tool_use",
            "extract_world_model",
            {
                "facts": [
                    {"content": "", "kind": "fact", "confidence": 0.5},           # empty content
                    {"content": "valid", "kind": "assumption", "confidence": 0.5},  # bad kind
                    {"content": "kept", "kind": "fact", "confidence": 0.7},
                    "not-a-dict",
                ],
                "hypotheses": [],
            },
        )
    ])
    _install_fake_anthropic(monkeypatch, resp)
    out = await kg.extract_world_model("text")
    assert [f["content"] for f in out["facts"]] == ["kept"]


@pytest.mark.asyncio
async def test_no_tool_block_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_anthropic(monkeypatch, _Resp([_Block("text", "n/a", {})]))
    out = await kg.extract_world_model("text")
    assert out["facts"] == [] and "error" in out


@pytest.mark.asyncio
async def test_api_exception_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    _install_fake_anthropic(monkeypatch, RuntimeError("boom"))
    out = await kg.extract_world_model("text")
    assert out == {"facts": [], "hypotheses": [], "error": "kg extraction failed"}
