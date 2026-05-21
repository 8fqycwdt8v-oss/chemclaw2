"""Regression tests for `build_chemclaw_mcp_server`.

The previous `@mcp.tool()` pattern was incompatible with
`claude-agent-sdk` 0.2 (`create_sdk_mcp_server` returns a dict, not an
object exposing a `.tool()` method). The agent runtime crashloop'd on
first /api/chat call. No test exercised this path, which is why CI
stayed green through 12 PRs while production was broken.

These tests run the actual build and verify:

  1. No AttributeError. The build returns the SDK's expected dict shape.
  2. Every tool we expect is present, by name.
  3. The `wrap_tool` adapter correctly forwards args + envelopes results
     into the `{"content": [{"type": "text", "text": ...}]}` SDK shape.
  4. Handler exceptions are caught and surfaced as
     `{"is_error": True, ...}` rather than crashing the agent loop.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import pytest


def test_build_chemclaw_mcp_server_returns_sdk_shape():
    """Smoke: build the server with stubbed deps and confirm the SDK
    contract — a dict containing `instance` and a registered tools
    list. Before the fix this raised `AttributeError: 'dict' object has
    no attribute 'tool'`."""
    from api.agent.tools import build_chemclaw_mcp_server

    server = build_chemclaw_mcp_server(
        user_id="u-test",
        session_id="s-test",
        session_factory=MagicMock(),
    )
    assert isinstance(server, dict)
    assert server.get("type") == "sdk"
    assert server.get("name") == "chemclaw2-tools"
    # `instance` is the mcp.server.lowlevel.Server the SDK runs.
    assert server.get("instance") is not None


def test_all_expected_tools_register():
    """Every tool the agent's system prompt references must be in the
    registered list. The current expected surface is 42 tools across the
    5 thematic modules; this test pins down what's exposed."""
    from api.agent.tools_campaign import build_campaign_tools
    from api.agent.tools_chem import build_chem_tools
    from api.agent.tools_external import build_external_tools
    from api.agent.tools_investigation import build_investigation_tools
    from api.agent.tools_knowledge import build_knowledge_tools

    sf = MagicMock()
    chem = build_chem_tools(sf)
    knowledge = build_knowledge_tools("u-test", sf)
    investigation = build_investigation_tools("u-test", "s-test", sf)
    external = build_external_tools("u-test", sf)
    campaign = build_campaign_tools("u-test", "s-test", sf)

    all_names = {t.name for t in chem + knowledge + investigation + external + campaign}

    expected = {
        # chem (5)
        "compound_similarity_search", "reaction_similarity_search",
        "suggest_conditions_from_neighbors", "list_reaction_outcomes",
        "substructure_search",
        # knowledge (8)
        "wiki_lookup", "lookup_knowledge", "register_paper", "paper_qa",
        "record_external_fact", "verify_citation", "record_contradiction",
        "lookup_regulatory_guidance",
        # investigation (13)
        "start_investigation", "list_investigations", "update_investigation_status",
        "world_model_add", "world_model_query", "world_model_supersede",
        "propose_hypothesis", "list_hypotheses", "rank_hypotheses",
        "retire_hypothesis", "run_code", "get_code_execution",
        "list_code_executions",
        # external (9)
        "web_search", "fetch_document", "eln_fetch_experiment",
        "ingest_eln_experiment", "record_manual_outcome", "name_to_structure",
        "patent_coverage", "propose_retrosynthesis",
        "propose_retrosynthesis_deep",
        # campaign (7)
        "start_synthesis_campaign", "confirm_synthesis_plan",
        "record_feedback", "register_compound_property",
        "record_predicted_conditions", "declare_campaign_parameter_space",
        "propose_next_conditions",
    }
    missing = expected - all_names
    extra = all_names - expected
    assert not missing, f"expected tools missing: {missing}"
    assert not extra, f"unexpected tools present: {extra}"
    assert len(all_names) == 42


# ── wrap_tool adapter behaviour ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_wrap_tool_forwards_kwargs_and_envelopes_result():
    """The adapter converts SDK-shape `args: dict` to **kwargs and
    JSON-serialises the handler's return value into the SDK content shape."""
    import json

    from api.agent.tool_adapter import wrap_tool

    async def double(value: int) -> dict[str, int]:
        """Return the value doubled."""
        return {"result": value * 2}

    sdk_tool = wrap_tool("double", double)
    assert sdk_tool.name == "double"
    assert sdk_tool.description == "Return the value doubled."

    response = await sdk_tool.handler({"value": 5})
    assert response["content"][0]["type"] == "text"
    assert json.loads(response["content"][0]["text"]) == {"result": 10}
    # Happy-path responses don't carry is_error.
    assert response.get("is_error") is not True


@pytest.mark.asyncio
async def test_wrap_tool_catches_handler_exceptions():
    """Unhandled exceptions in the inner handler must NOT propagate into
    the SDK runtime — they would crash the agent loop. The adapter
    catches and surfaces `is_error: True` with a generic message
    (CLAUDE.md §security-4 — no exception text to the client)."""
    from api.agent.tool_adapter import wrap_tool

    async def boom() -> dict[str, int]:
        """Always raises."""
        raise RuntimeError("internal DB blew up — passwords inside: hunter2")

    sdk_tool = wrap_tool("boom", boom)
    response = await sdk_tool.handler({})
    assert response.get("is_error") is True
    text = response["content"][0]["text"]
    assert text == "internal tool error"
    # Exception detail must not leak.
    assert "hunter2" not in text
    assert "RuntimeError" not in text


@pytest.mark.asyncio
async def test_wrap_tool_drops_unexpected_args():
    """If the model sends a key not in the schema, the adapter drops it
    so the inner handler doesn't TypeError on an unexpected kwarg."""
    from api.agent.tool_adapter import wrap_tool

    async def echo(value: str) -> dict[str, str]:
        """Echo the value."""
        return {"got": value}

    sdk_tool = wrap_tool("echo", echo)
    # `extra_garbage` is silently dropped.
    response = await sdk_tool.handler({"value": "hi", "extra_garbage": 42})
    assert response.get("is_error") is not True
    import json
    assert json.loads(response["content"][0]["text"]) == {"got": "hi"}


@pytest.mark.asyncio
async def test_wrap_tool_handles_non_serialisable_return():
    """When the inner handler returns something json.dumps can't handle
    (with default=str fallback), surface a generic error envelope rather
    than crashing the agent loop."""
    from api.agent.tool_adapter import wrap_tool

    class _Unserialisable:
        def __repr__(self) -> str:
            raise RuntimeError("repr blew up")

    async def returns_garbage() -> dict[str, Any]:
        """Return something that can't be JSON-encoded."""
        return {"x": _Unserialisable()}

    sdk_tool = wrap_tool("returns_garbage", returns_garbage)
    response = await sdk_tool.handler({})
    assert response.get("is_error") is True
    assert "result not serialisable" in response["content"][0]["text"]
