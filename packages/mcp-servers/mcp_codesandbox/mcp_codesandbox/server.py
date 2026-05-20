"""MCP server exposing the subprocess-based Python sandbox.

Single tool `run_python`. The api-layer wrapper (api/agent/tools.py) calls
the sandbox library directly for in-process latency wins, but this stdio
server is still useful for standalone testing and gives the agent runtime
a uniform MCP surface alongside molfp / rxnfp / retrosynth / rxn_conditions.
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone

from mcp.server.fastmcp import FastMCP

from mcp_codesandbox.sandbox import (
    CPU_SECONDS_DEFAULT,
    MEMORY_BYTES_DEFAULT,
    WALL_SECONDS_DEFAULT,
    run_python,
    summary,
)


class JsonFormatter(logging.Formatter):
    """Single-line JSON to stderr; stdout is JSON-RPC."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname.lower(),
            "component": "mcp-codesandbox",
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
    return logging.getLogger("mcp_codesandbox")


log = _configure_logging()
mcp = FastMCP("mcp-codesandbox")


@mcp.tool()
async def execute(
    code: str,
    cpu_seconds: int = CPU_SECONDS_DEFAULT,
    wall_seconds: int = WALL_SECONDS_DEFAULT,
    memory_bytes: int = MEMORY_BYTES_DEFAULT,
) -> dict:
    """Run a Python snippet inside the resource-limited subprocess sandbox.

    Returns: {exit_code, status, duration_ms, stdout, stderr}
      - status: 'completed' (incl. non-zero exit), 'timeout', 'killed', 'error'
      - exit_code: 124 on wall-clock timeout, 137 on SIGKILL

    Hard caps: CPU≤300s, memory ≤512 MB, stdout ≤1 MB, stderr ≤256 KB,
    source code ≤200 KB. See sandbox.py for the trust-boundary writeup.
    """
    result = await run_python(
        code,
        cpu_seconds=cpu_seconds,
        wall_seconds=wall_seconds,
        memory_bytes=memory_bytes,
    )
    log.info(
        "execute_done",
        extra={
            "status": result.status,
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            "stdout_bytes": len(result.stdout),
            "stderr_bytes": len(result.stderr),
        },
    )
    return summary(result)


def main():
    log.info("mcp_server_starting", extra={"name": mcp.name, "pid": os.getpid()})
    try:
        mcp.run(transport="stdio")
    except Exception:
        log.exception("mcp_server_crashed")
        raise


if __name__ == "__main__":
    main()
