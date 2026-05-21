"""Adapter from chemclaw2's tool style to the claude-agent-sdk's SDK MCP shape.

The SDK expects each tool to be an `SdkMcpTool` (built via the top-level
`@tool(name, description, schema)` decorator) with a handler that takes
`args: dict[str, Any]` and returns `{"content": [{"type": "text", ...}],
"is_error": bool}`.

Our tools are written in a more Pythonic style: explicit kwargs in the
signature, raw dict return. `wrap_tool` bridges the two so the body of
every tool can stay unchanged:

  - Introspect the function signature to build the JSON-schema dict.
  - Forward `args[k]` as `kwargs[k]` to the inner handler.
  - JSON-serialise the return value into the SDK's content shape.
  - Catch unexpected exceptions, log them, and surface a generic
    `is_error: True` envelope (CLAUDE.md §security-4 — don't leak
    internal exception text to the agent).

The first non-blank line of the docstring becomes the tool description.

This adapter replaces the pre-existing `@mcp.tool()` pattern which was
incompatible with `claude-agent-sdk` >=0.2 (`create_sdk_mcp_server`
returns a plain dict, not an object with a `.tool()` method — see the
"Discovered during refactor sweep" entry in BACKLOG.md).
"""
from __future__ import annotations

import inspect
import json
import logging
from typing import Any, Awaitable, Callable, get_type_hints

from claude_agent_sdk import SdkMcpTool, tool

logger = logging.getLogger(__name__)


def _first_sentence(doc: str | None) -> str:
    """Take the first non-blank paragraph of a docstring, collapsed to one line."""
    if not doc:
        return ""
    para = doc.strip().split("\n\n")[0]
    return " ".join(para.split())[:500]


def _build_schema(handler: Callable[..., Any]) -> dict[str, Any]:
    """Build a schema dict mapping each annotated parameter to its Python type.

    The SDK runs each type through its own `_python_type_to_json_schema`
    which handles `str | None`, `list[T]`, `dict`, etc. Parameters without
    annotations are skipped (they would default to `{"type": "string"}`,
    which is misleading).
    """
    sig = inspect.signature(handler)
    try:
        hints = get_type_hints(handler)
    except Exception:
        hints = {}
    schema: dict[str, Any] = {}
    for param_name, param in sig.parameters.items():
        annotation = hints.get(param_name, param.annotation)
        if annotation is inspect.Parameter.empty:
            continue
        schema[param_name] = annotation
    return schema


def wrap_tool(
    name: str,
    handler: Callable[..., Awaitable[dict[str, Any]]],
    *,
    description: str | None = None,
) -> SdkMcpTool[Any]:
    """Adapt a kwargs-style async handler into an `SdkMcpTool`.

    The wrapped handler accepts the SDK's `args: dict` envelope, forwards
    the corresponding kwargs to `handler`, and JSON-serialises the result
    into the SDK content shape. Unrecognised keys in `args` are dropped
    (the SDK already validates against the schema we declared).
    """
    desc = description if description is not None else _first_sentence(handler.__doc__)
    schema = _build_schema(handler)
    sig = inspect.signature(handler)
    accepted = set(sig.parameters)

    @tool(name, desc, schema)
    async def _adapted(args: dict[str, Any]) -> dict[str, Any]:
        # Drop any unexpected keys the model might have sent — the schema
        # is the source of truth, and TypeError on the inner call would
        # be a confusing failure mode.
        kwargs = {k: v for k, v in args.items() if k in accepted}
        try:
            result = await handler(**kwargs)
        except Exception:
            logger.exception("tool_handler_failed tool=%s", name)
            return {
                "content": [
                    {"type": "text", "text": "internal tool error"},
                ],
                "is_error": True,
            }
        # Our tools' "error" dicts come back as regular results; the agent
        # is already trained to read them. Surface the `is_error` envelope
        # only for the wrapper-level catch above.
        try:
            text = json.dumps(result, default=str)
        except Exception:
            # json.dumps can raise TypeError/ValueError for unsupported
            # types, but `default=str` may also surface arbitrary
            # exceptions out of a broken `__str__` / `__repr__`.
            logger.exception("tool_result_not_json_serialisable tool=%s", name)
            return {
                "content": [
                    {"type": "text", "text": "internal tool error: result not serialisable"},
                ],
                "is_error": True,
            }
        return {"content": [{"type": "text", "text": text}]}

    return _adapted
