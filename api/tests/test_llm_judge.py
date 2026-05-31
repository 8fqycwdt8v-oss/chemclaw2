"""Unit tests for the shared LLM-as-judge helper.

All paths use mocked provider clients — no real LLM calls. Covers
provider/model resolution from env, the success JSON path, and the
fail-soft branches (missing key, no JSON, parse error).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest


def _anthropic_client_returning(text_body: str) -> Any:
    block = SimpleNamespace(type="text", text=text_body)
    resp = SimpleNamespace(content=[block])
    client = SimpleNamespace(messages=SimpleNamespace(create=AsyncMock(return_value=resp)))
    return client


def _openai_client_returning(text_body: str) -> Any:
    msg = SimpleNamespace(message=SimpleNamespace(content=text_body))
    resp = SimpleNamespace(choices=[msg])
    client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=AsyncMock(return_value=resp)))
    )
    return client


def test_resolve_judge_model_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.agent.llm_judge import resolve_judge_model
    for var in ("JUDGE_PROVIDER", "VLM_PROVIDER", "VLM_MODEL",
                "ANTHROPIC_JUDGE_MODEL", "OPENAI_JUDGE_MODEL"):
        monkeypatch.delenv(var, raising=False)
    provider, model = resolve_judge_model("text")
    assert provider == "anthropic"
    assert "haiku" in model.lower()
    vprovider, vmodel = resolve_judge_model("vision")
    assert vprovider == "openai"
    assert vmodel == "gpt-4o-mini"


def test_resolve_judge_model_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    from api.agent.llm_judge import resolve_judge_model
    monkeypatch.setenv("JUDGE_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_JUDGE_MODEL", "gpt-4o")
    assert resolve_judge_model("text") == ("openai", "gpt-4o")


async def test_judge_json_anthropic_success(monkeypatch: pytest.MonkeyPatch) -> None:
    import api.agent.llm_judge as mod
    client = _anthropic_client_returning('```json\n{"ok": true, "n": 3}\n```')
    monkeypatch.setattr(mod, "_get_anthropic_client", lambda: client)
    parsed, err = await mod.judge_json("p", provider="anthropic", model="m")
    assert err is None
    assert parsed == {"ok": True, "n": 3}


async def test_judge_json_openai_image_path(monkeypatch: pytest.MonkeyPatch) -> None:
    import api.agent.llm_judge as mod
    client = _openai_client_returning('{"severity": "minor"}')
    monkeypatch.setattr(mod, "_get_openai_client", lambda: client)
    parsed, err = await mod.judge_json(
        "p", provider="openai", model="gpt-4o-mini", images=["QUJD"],
    )
    assert err is None
    assert parsed == {"severity": "minor"}
    # The image was forwarded as a data-url image_url content block.
    sent = client.chat.completions.create.await_args.kwargs["messages"][0]["content"]
    assert any(b.get("type") == "image_url" for b in sent)


async def test_judge_json_missing_key_fails_soft(monkeypatch: pytest.MonkeyPatch) -> None:
    import api.agent.llm_judge as mod
    monkeypatch.setattr(mod, "_get_anthropic_client", lambda: None)
    parsed, err = await mod.judge_json("p", provider="anthropic", model="m")
    assert parsed is None
    assert err is not None and "ANTHROPIC_API_KEY" in err


async def test_judge_json_no_json_in_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    import api.agent.llm_judge as mod
    client = _anthropic_client_returning("I refuse to answer in JSON.")
    monkeypatch.setattr(mod, "_get_anthropic_client", lambda: client)
    parsed, err = await mod.judge_json("p", provider="anthropic", model="m")
    assert parsed is None
    assert err is not None and "no JSON" in err


async def test_judge_json_non_object_root(monkeypatch: pytest.MonkeyPatch) -> None:
    import api.agent.llm_judge as mod
    client = _anthropic_client_returning("```json\n[1, 2, 3]\n```")
    monkeypatch.setattr(mod, "_get_anthropic_client", lambda: client)
    parsed, err = await mod.judge_json("p", provider="anthropic", model="m")
    # A JSON array is extracted as text but is not a dict object → no JSON object found.
    assert parsed is None
    assert err is not None
