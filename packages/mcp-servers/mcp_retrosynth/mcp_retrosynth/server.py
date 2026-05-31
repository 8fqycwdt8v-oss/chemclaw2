"""MCP server exposing template-based retrosynthetic disconnections.

This is a small, deterministic helper — given a target SMILES, apply a
curated set of reaction-SMARTS templates and return the precursor sets
each disconnection produces. It is NOT a substitute for AiZynthFinder /
ASKCOS / IBM RXN on full multi-step planning; it is a fast 1-step
"what plausible disconnections exist?" answer the agent can use to seed
deeper analysis.
"""
from __future__ import annotations

import os
import time

from mcp.server.fastmcp import FastMCP
from mcp_chemclaw_shared import configure_logging

from mcp_retrosynth.disconnect import list_transforms, propose_disconnections

log = configure_logging("mcp-retrosynth")
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
