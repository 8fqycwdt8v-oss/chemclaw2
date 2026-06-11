"""Shared structured-logging setup for the chemclaw2 MCP stdio servers.

Every MCP server speaks JSON-RPC on stdout, so its logs must go to stderr as
single-line JSON. This module is the single copy of that formatter +
`configure_logging` setup; each server imports it instead of hand-rolling its
own (the seven near-identical copies this replaces are the duplication the
backlog flagged once a fourth server landed).
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import UTC, datetime
from typing import Any

# Record attributes that are logging internals, not structured fields the
# server attached via `extra=`. Everything else on the record is emitted.
_RESERVED = frozenset((
    "args", "msg", "name", "exc_info", "exc_text", "stack_info",
    "lineno", "funcName", "created", "msecs", "relativeCreated",
    "thread", "threadName", "processName", "process", "filename",
    "module", "pathname", "levelname", "levelno",
))


class JsonFormatter(logging.Formatter):
    """Emit single-line JSON to stderr. Stdout is reserved for JSON-RPC."""

    def __init__(self, component: str) -> None:
        super().__init__()
        self._component = component

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.lower(),
            "component": self._component,
            "event": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for k, v in record.__dict__.items():
            if k in _RESERVED:
                continue
            payload[k] = v
        # default=str so non-JSON-native extras (e.g. numpy scalars from
        # mcp_tabular) serialise instead of raising inside the log handler.
        return json.dumps(payload, default=str)


# Input-size ceilings shared by the chemistry MCP servers (third-copy rule).
# Real chemistry SMILES top out well under 1k chars; anything beyond these
# bounds is malformed input or a DoS attempt. Reaction SMILES
# (reactants>>products) get a wider bound than single-molecule SMILES.
MAX_SMILES_LEN = 10_000
MAX_REACTION_SMILES_LEN = 20_000


def configure_logging(component: str) -> logging.Logger:
    """Route the root logger to stderr as JSON and return the server logger.

    `component` is the hyphenated server name (e.g. ``"mcp-molfp"``); the
    returned logger uses the underscore form (``"mcp_molfp"``) so it matches
    the module's own ``getLogger`` name.
    """
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter(component))
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(os.environ.get("MCP_LOG_LEVEL", "INFO"))
    return logging.getLogger(component.replace("-", "_"))


def run_server(mcp: Any, component: str, init_fn: Any = None) -> None:
    """Standard MCP stdio server entrypoint: logging setup, start log, run.

    Replaces the near-identical ``main()`` scaffolding each server carried.
    The start log uses the ``server`` key — NOT ``name`` — because ``extra``
    keys that collide with built-in LogRecord attributes (``name`` is one)
    make ``logging`` raise ``KeyError`` and killed every server at startup.

    ``init_fn``, when given, runs after logging is configured and before the
    stdio loop starts (env-var reads, SDK wrapper setup).
    """
    log = configure_logging(component)
    log.info("mcp_server_starting", extra={"server": mcp.name, "pid": os.getpid()})
    try:
        if init_fn is not None:
            init_fn()
        mcp.run(transport="stdio")
    except Exception:
        log.exception("mcp_server_crashed")
        raise


__all__ = ["MAX_REACTION_SMILES_LEN", "MAX_SMILES_LEN", "JsonFormatter", "configure_logging", "run_server"]
