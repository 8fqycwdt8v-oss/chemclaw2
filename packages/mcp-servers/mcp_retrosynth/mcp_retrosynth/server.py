"""MCP server exposing template-based retrosynthetic disconnections.

This is a small, deterministic helper — given a target SMILES, apply a
curated set of reaction-SMARTS templates and return the precursor sets
each disconnection produces. It is NOT a substitute for AiZynthFinder /
ASKCOS / IBM RXN on full multi-step planning; it is a fast 1-step
"what plausible disconnections exist?" answer the agent can use to seed
deeper analysis.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from datetime import UTC, datetime

from mcp.server.fastmcp import FastMCP

from mcp_retrosynth.disconnect import list_transforms, propose_disconnections


class JsonFormatter(logging.Formatter):
    """Emit single-line JSON to stderr. Stdout is reserved for JSON-RPC."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.fromtimestamp(record.created, UTC).isoformat(),
            "level": record.levelname.lower(),
            "component": "mcp-retrosynth",
            "event": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for k, v in record.__dict__.items():
            if k in ("args", "msg", "name", "exc_info", "exc_text", "stack_info",
                     "lineno", "funcName", "created", "msecs", "relativeCreated",
                     "thread", "threadName", "processName", "process", "filename",
                     "module", "pathname", "levelname", "levelno"):
                continue
            payload[k] = v
        return json.dumps(payload)


def _configure_logging() -> logging.Logger:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(os.environ.get("MCP_LOG_LEVEL", "INFO"))
    return logging.getLogger("mcp_retrosynth")


log = _configure_logging()
mcp = FastMCP("mcp-retrosynth")

MAX_SMILES_LEN = 10_000


@mcp.tool()
def disconnect(target_smiles: str, max_routes: int = 5) -> dict:
    """Propose one-step retrosynthetic disconnections for a target SMILES.

    Returns up to max_routes precursor sets ranked by per-template confidence
    (a static prior, not a learned score). Each route has fields:
      - transform: short label, e.g. "amide_bond"
      - precursors: list of canonical SMILES
      - confidence: 0–1 prior
      - notes: short rationale
    """
    if len(target_smiles) > MAX_SMILES_LEN:
        raise ValueError(f"target_smiles exceeds {MAX_SMILES_LEN} chars")
    if not (1 <= max_routes <= 20):
        raise ValueError("max_routes must be between 1 and 20")
    t0 = time.monotonic()
    routes = propose_disconnections(target_smiles, max_routes)
    log.info(
        "disconnect_done",
        extra={"smiles_len": len(target_smiles), "n_routes": len(routes),
               "duration_ms": int((time.monotonic() - t0) * 1000)},
    )
    return {"target": target_smiles, "routes": routes, "total": len(routes)}


@mcp.tool()
def list_supported_transforms() -> dict:
    """List every retrosynthetic transform the server knows about.

    Useful for the agent to inspect coverage before falling back to a
    heavier external retrosynthesis service.
    """
    return {"transforms": list_transforms()}


def main():
    log.info("mcp_server_starting", extra={"name": mcp.name, "pid": os.getpid()})
    try:
        mcp.run(transport="stdio")
    except Exception:
        log.exception("mcp_server_crashed")
        raise


if __name__ == "__main__":
    main()
