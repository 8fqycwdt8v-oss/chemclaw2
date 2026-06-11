"""Retrosynthesis tool: single-step disconnections via the in-process
RDKit-template library (`mcp_retrosynth`).

`build_retrosynth_tools(user_id, session_factory)` returns the
`SdkMcpTool` list.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from claude_agent_sdk import SdkMcpTool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.agent.tool_adapter import wrap_tool

logger = logging.getLogger(__name__)


def build_retrosynth_tools(
    user_id: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> list[SdkMcpTool[Any]]:
    """Build the single-step retrosynthesis tool."""

    async def propose_retrosynthesis(
        target_smiles: str,
        max_routes: int = 5,
    ) -> dict[str, Any]:
        """Propose one-step retrosynthetic disconnections for a target SMILES.

        Calls the mcp-retrosynth subprocess (RDKit + curated reaction-template
        library) and returns precursor sets keyed by transform name. Use the
        output to seed `confirm_synthesis_plan` or for further analog work.
        Returns {target, routes: [{transform, precursors, confidence}], total}.
        """
        s = target_smiles.strip()
        if not s or len(s) > 1000:
            return {"error": "target_smiles must be 1-1000 chars"}
        if max_routes < 1 or max_routes > 20:
            return {"error": "max_routes must be between 1 and 20"}

        # Use the in-process retrosynthesis library directly when available —
        # the same code the stdio MCP server runs. Avoids subprocess overhead
        # for what is a pure CPU + RDKit call.
        try:
            from mcp_retrosynth.disconnect import propose_disconnections
        except ImportError:
            return {"error": "Retrosynthesis backend not installed (mcp_retrosynth)"}
        try:
            routes = await asyncio.get_running_loop().run_in_executor(
                None, propose_disconnections, s, max_routes,
            )
        except ValueError as e:
            return {"error": str(e)}
        except Exception:
            logger.exception("retrosynth_failed smiles_len=%d", len(s))
            return {"error": "Retrosynthesis proposal failed"}
        return {
            "target": s,
            "routes": routes,
            "total": len(routes),
        }

    return [
        wrap_tool("propose_retrosynthesis", propose_retrosynthesis),
    ]
