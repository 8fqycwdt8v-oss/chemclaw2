"""Adapter from chemclaw2's tool style to the claude-agent-sdk's SDK MCP shape.

The SDK expects each tool to be an `SdkMcpTool` (built via the top-level
`@tool(name, description, schema)` decorator) with a handler that takes
`args: dict[str, Any]` and returns `{"content": [{"type": "text", ...}],
"is_error": bool}`.

Our tools are written in a more Pythonic style: explicit kwargs in the
signature, raw dict return. `wrap_tool` bridges the two so the body of
every tool can stay unchanged:

  - Introspect the function signature to build a full JSON Schema:
    every parameter's type is mapped, parameters with no default land
    in `required`, and `T | None` annotations allow null in the
    property type.
  - Forward `args[k]` as `kwargs[k]` to the inner handler.
  - JSON-serialise the return value into the SDK's content shape.
  - Catch unexpected exceptions, log them, and surface a generic
    `is_error: True` envelope (CLAUDE.md §security-4 — don't leak
    internal exception text to the agent).

The first non-blank line of the docstring becomes the tool description.

This adapter replaces the pre-existing `@mcp.tool()` pattern which was
incompatible with `claude-agent-sdk` >=0.2 (`create_sdk_mcp_server`
returns a plain dict, not an object with a `.tool()` method).
"""
from __future__ import annotations

import inspect
import json
import logging
import types
from typing import Any, Awaitable, Callable, Union, get_args, get_origin, get_type_hints

from claude_agent_sdk import SdkMcpTool, tool

logger = logging.getLogger(__name__)


def _first_sentence(doc: str | None) -> str:
    """Take the first non-blank paragraph of a docstring, collapsed to one line."""
    if not doc:
        return ""
    para = doc.strip().split("\n\n")[0]
    return " ".join(para.split())[:500]


def _property_schema(py_type: Any) -> dict[str, Any]:
    """Build a JSON-schema property dict for a single Python type annotation.

    Supports the type forms our tools actually use:
      - primitives: str / int / float / bool
      - list[T], list
      - dict[K, V], dict
      - Optional[T] / `T | None` — adds 'null' to the type or anyOf
      - Union with two+ non-None members — anyOf
    Unknown shapes fall back to `{}` (accept anything) rather than
    `{"type": "string"}`, which would silently reject other types.
    """
    if py_type is str:
        return {"type": "string"}
    if py_type is int:
        return {"type": "integer"}
    if py_type is float:
        return {"type": "number"}
    if py_type is bool:
        return {"type": "boolean"}

    origin = get_origin(py_type)

    # `T | None` / `Optional[T]` / `Union[A, B, None]`
    if origin is Union or isinstance(py_type, types.UnionType):
        args = [a for a in get_args(py_type)]
        nullable = type(None) in args
        non_none = [a for a in args if a is not type(None)]
        if not non_none:
            return {"type": "null"}
        if len(non_none) == 1:
            inner = _property_schema(non_none[0])
            if nullable and "type" in inner and isinstance(inner["type"], str):
                # `str | None` → {"type": ["string", "null"]}
                return {**inner, "type": [inner["type"], "null"]}
            if nullable:
                # Fallback for non-{type:str} schemas (e.g. anyOf, no type key).
                return {"anyOf": [inner, {"type": "null"}]}
            return inner
        members = [_property_schema(a) for a in non_none]
        if nullable:
            members.append({"type": "null"})
        return {"anyOf": members}

    if origin is list:
        item_args = get_args(py_type)
        if item_args:
            return {"type": "array", "items": _property_schema(item_args[0])}
        return {"type": "array"}
    if py_type is list:
        return {"type": "array"}

    if origin is dict or py_type is dict:
        return {"type": "object"}

    # Unrecognised — accept anything rather than silently misclassifying.
    return {}


def _build_input_schema(handler: Callable[..., Any]) -> dict[str, Any]:
    """Build a full JSON Schema object for the handler's signature.

    Parameters with no default value land in `required`; parameters with
    defaults stay optional. `T | None` annotations allow null on the
    property type so the model can send None when a default would be
    semantically meaningful.
    """
    sig = inspect.signature(handler)
    try:
        hints = get_type_hints(handler)
    except Exception:
        hints = {}

    properties: dict[str, Any] = {}
    required: list[str] = []
    for param_name, param in sig.parameters.items():
        annotation = hints.get(param_name, param.annotation)
        if annotation is inspect.Parameter.empty:
            properties[param_name] = {}
        else:
            properties[param_name] = _property_schema(annotation)
        if param.default is inspect.Parameter.empty:
            required.append(param_name)

    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
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
    schema = _build_input_schema(handler)
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
