"""Tests for the McpBuilder adapter that preserves chemclaw2's
`@mcp.tool()` closure pattern on top of claude-agent-sdk's
`create_sdk_mcp_server(tools=[...])` API.

Existence of these tests pins down the regression that caused this
adapter to be written: the SDK's `create_sdk_mcp_server(name)` returns
a plain dict with no `.tool()` attribute. Calling the chemclaw2
`@mcp.tool()` pattern directly on that dict raises AttributeError —
which broke every chat turn in production until this adapter was
introduced. The test below FAILS on the broken path and passes on the
adapter, so a future SDK change that breaks the same way gets caught.
"""
from __future__ import annotations

import json
from typing import Any


def test_raw_sdk_dict_does_not_support_tool_decorator() -> None:
    """Regression pin: the SDK's bare `create_sdk_mcp_server(name)`
    returns a `dict` with no `.tool` method. This test documents the
    motivation for the McpBuilder adapter — if a future SDK release
    starts returning an object with `.tool()`, this test will fail and
    the adapter becomes optional."""
    from claude_agent_sdk import create_sdk_mcp_server
    config = create_sdk_mcp_server("test-server")
    assert isinstance(config, dict)
    assert not hasattr(config, "tool")
    # The dict contains an MCP Server `instance`, but Server itself
    # only has `call_tool` (an incoming-request handler) — not a tool
    # registration decorator.
    instance = config.get("instance")
    assert instance is not None
    assert not hasattr(instance, "tool")


async def test_mcp_builder_collects_tools_and_builds_config() -> None:
    """Happy path: a function decorated with `@mcp.tool()` is collected,
    and `.build()` returns an SDK-compatible McpSdkServerConfig dict."""
    from api.agent._sdk_adapter import McpBuilder

    mcp = McpBuilder("test-server")

    @mcp.tool()
    async def add_one(x: int) -> dict[str, Any]:
        """Add one to x."""
        return {"result": x + 1}

    assert mcp.tool_count == 1

    config = mcp.build()
    assert isinstance(config, dict)
    assert config["type"] == "sdk"
    assert config["name"] == "test-server"
    assert config["instance"] is not None


async def test_mcp_builder_envelopes_plain_dict_return() -> None:
    """chemclaw2 tools return plain `dict[str, Any]`. The adapter must
    wrap that into the SDK's `{"content": [{"type": "text", "text":
    "<json>"}]}` shape."""
    from api.agent._sdk_adapter import _envelope

    enveloped = _envelope({"result": 42, "ok": True})
    assert enveloped["content"][0]["type"] == "text"
    parsed = json.loads(enveloped["content"][0]["text"])
    assert parsed == {"result": 42, "ok": True}


async def test_mcp_builder_passes_through_sdk_shape() -> None:
    """If a tool already returns the SDK content-block shape (e.g. an
    explicit error response), don't double-wrap it."""
    from api.agent._sdk_adapter import McpBuilder

    mcp = McpBuilder("t")

    @mcp.tool()
    async def already_shaped(x: str) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": "pre-shaped"}]}

    # The tool's adapter is the FIRST item we collected.
    sdk_tool = mcp._tools[0]
    result = await sdk_tool.handler({"x": "anything"})
    assert result["content"][0]["text"] == "pre-shaped"
    # NOT JSON-encoded into another nested content block.
    assert not result["content"][0]["text"].startswith("{")


async def test_mcp_builder_envelopes_str_return() -> None:
    """Tools that return a bare string get text-block-wrapped without
    JSON-encoding (otherwise you'd get `"\"hi\""` instead of `"hi"`)."""
    from api.agent._sdk_adapter import _envelope

    enveloped = _envelope("hi")
    assert enveloped == {"content": [{"type": "text", "text": "hi"}]}


async def test_mcp_builder_handles_bad_args() -> None:
    """The SDK passes args as a dict; if a kwarg the function requires
    is missing, the adapter must surface the binding error as a tool-
    level response rather than crashing the entire request."""
    from api.agent._sdk_adapter import McpBuilder

    mcp = McpBuilder("t")

    @mcp.tool()
    async def needs_required(a: str, b: int) -> dict[str, Any]:
        return {"a": a, "b": b}

    sdk_tool = mcp._tools[0]
    # Call missing required `b`.
    result = await sdk_tool.handler({"a": "x"})
    text = result["content"][0]["text"]
    parsed = json.loads(text)
    assert "error" in parsed
    assert "invalid args" in parsed["error"]


async def test_mcp_builder_schema_derives_from_type_hints() -> None:
    """The adapter's schema-derivation logic should map function type
    hints into the SDK's `{name: type}` schema dict, skipping unhinted
    params."""
    from api.agent._sdk_adapter import _schema_for

    def fn(x: str, n: int = 5, optional_unhinted=None) -> None:
        ...

    schema = _schema_for(fn)
    assert schema == {"x": str, "n": int}


def test_build_chemclaw_mcp_server_does_not_crash() -> None:
    """End-to-end smoke: build_chemclaw_mcp_server runs without raising.
    Before this PR, the @mcp.tool() decorator chain raised
    AttributeError at the first decoration. Now should succeed and
    return an SDK config with all tools registered.

    Uses no DB / no network — purely exercises the module-load + tool-
    factory function path. Async test fixtures aren't needed because
    the tool BODIES never run, only the registrations."""
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from api.agent.tools import build_chemclaw_mcp_server

    # A bare async_sessionmaker built from `None` would crash on use,
    # but tool registration doesn't call any session — it only stashes
    # the factory in the closures.
    config = build_chemclaw_mcp_server(
        user_id="u-test",
        session_id="sess-test",
        session_factory=async_sessionmaker(bind=None),  # type: ignore[arg-type]
    )
    assert isinstance(config, dict)
    assert config["type"] == "sdk"
    assert config["name"] == "chemclaw2-tools"
    # Smoke: at least the dozen-plus tools we know about should be
    # registered. The SDK's Server.list_tools() returns a handler, not
    # the list directly, so just check `instance` is non-None.
    assert config["instance"] is not None
