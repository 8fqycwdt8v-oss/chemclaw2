"""Adapter that preserves chemclaw2's `@mcp.tool()` closure pattern on
top of claude-agent-sdk 0.2.82's `create_sdk_mcp_server(tools=[...])`
API.

The SDK's `create_sdk_mcp_server(name)` returns an `McpSdkServerConfig`
TypedDict with no `.tool` attribute. Calling `@mcp.tool()` on it raises
`AttributeError: 'dict' object has no attribute 'tool'`. The
chemclaw2 agent runtime relies on the closure pattern to inject
`user_id` / `session_id` / `session_factory` into each tool body —
rewriting every tool to extract args from a single `args: dict` +
restructuring closures would touch 40+ sites across 4 modules.

This adapter wraps the SDK's top-level `@tool(name, description,
input_schema)` factory and collects the resulting `SdkMcpTool`
instances in a small builder. At the end of the tool-factory function,
`build()` returns the same `McpSdkServerConfig` dict the SDK would
produce, ready to pass to `ClaudeAgentOptions.mcp_servers`.

Conventions matched to existing chemclaw2 closure-tool signatures:
  - Tool name derives from the wrapped function's `__name__`.
  - Description derives from the first line of the docstring.
  - Input schema is built from the function's type hints
    (`{param_name: python_type}` dict — the SDK's permissive form).
  - The wrapped chemclaw2 function takes plain kwargs (`def foo(arg:
    str, n: int = 5)`) and returns a plain `dict[str, Any]`. The
    adapter wraps this in an SDK-compatible `args: dict` →
    `{"content": [{"type": "text", "text": "<json>"}]}` shim.
"""
from __future__ import annotations

import inspect
import json
import logging
from collections.abc import Callable
from typing import Any, get_type_hints

from claude_agent_sdk import create_sdk_mcp_server
from claude_agent_sdk import tool as _sdk_tool

logger = logging.getLogger(__name__)


class McpBuilder:
    """Drop-in replacement for the (broken) chemclaw2 idiom
    `mcp = create_sdk_mcp_server(name); @mcp.tool()`.

    Use:
        mcp = McpBuilder("chemclaw2-tools")

        @mcp.tool()
        async def my_tool(x: str, n: int = 5) -> dict[str, Any]:
            ...

        return mcp.build()   # → McpSdkServerConfig dict
    """

    def __init__(self, name: str, version: str = "1.0.0") -> None:
        self._name = name
        self._version = version
        self._tools: list[Any] = []

    def tool(
        self,
        name: str | None = None,
        description: str | None = None,
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator. Collects the wrapped function as an SDK-compatible
        tool. Returns the original function unchanged so it stays
        importable/callable from tests if needed."""

        def decorator(fn: Callable[..., Any]) -> Callable[..., Any]:
            tool_name = name or fn.__name__
            doc = (fn.__doc__ or "").strip()
            tool_desc = description or (doc.split("\n")[0] if doc else fn.__name__)
            schema = _schema_for(fn)

            @_sdk_tool(tool_name, tool_desc, schema)
            async def _adapter(args: dict[str, Any]) -> dict[str, Any]:
                try:
                    result = await fn(**args)
                except TypeError as e:
                    # Likely a missing-required-kwarg or extra-kwarg from
                    # the SDK's args dict. Surface as a tool-level
                    # error rather than crashing the whole request.
                    logger.warning(
                        "tool=%s arg-binding failed: %s", tool_name, e,
                    )
                    return _envelope({"error": f"invalid args: {e}"})
                if isinstance(result, dict) and "content" in result:
                    # Already in SDK shape (handler returns content blocks
                    # directly) — pass through unchanged.
                    return result
                return _envelope(result)

            self._tools.append(_adapter)
            return fn

        return decorator

    def build(self) -> Any:
        """Return the `McpSdkServerConfig` TypedDict the SDK's
        `ClaudeAgentOptions.mcp_servers` expects. Annotated as `Any`
        rather than `dict[str, Any]` because `McpSdkServerConfig` is
        a `TypedDict` and mypy treats those as structurally distinct
        from plain dicts in covariant return positions."""
        return create_sdk_mcp_server(
            self._name, version=self._version, tools=self._tools,
        )

    @property
    def tool_count(self) -> int:
        """For diagnostics / tests."""
        return len(self._tools)


def _envelope(result: Any) -> dict[str, Any]:
    """Wrap a chemclaw2-style plain return value in SDK content-block
    shape. JSON-encode dicts/lists; str-coerce everything else.

    Mirrors what `claude_agent_sdk.create_sdk_mcp_server`'s internal
    `_handle_call_tool` does when the tool returns a dict without an
    explicit `content` key (per the SDK source).
    """
    if isinstance(result, str):
        text = result
    else:
        try:
            text = json.dumps(result, default=str)
        except (TypeError, ValueError) as e:
            logger.warning("tool result JSON-encode failed: %s", e)
            text = str(result)
    return {"content": [{"type": "text", "text": text}]}


def _schema_for(fn: Callable[..., Any]) -> dict[str, Any]:
    """Build the `{param_name: python_type}` schema dict from a
    function's type hints. Skips `self` / `cls` and unhinted params
    (the SDK treats missing types as permissive)."""
    sig = inspect.signature(fn)
    try:
        hints = get_type_hints(fn)
    except Exception:
        # Forward refs that can't resolve at call time — fall back to
        # an empty schema so the SDK accepts the tool.
        hints = {}
    schema: dict[str, Any] = {}
    for param_name, _param in sig.parameters.items():
        if param_name in ("self", "cls"):
            continue
        if param_name not in hints:
            # Untyped param — leave out so the SDK doesn't enforce a
            # JSON type for it.
            continue
        schema[param_name] = hints[param_name]
    return schema
