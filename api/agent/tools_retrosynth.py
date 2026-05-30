"""Retrosynthesis tools split out of `tools_external.py`.

Two tools wrapping the local RDKit-template library (single-step) and
AiZynthFinder (multi-step, opt-in via [retrosynth] extras):

  - `propose_retrosynthesis` — fast single-step disconnections via
    the in-process mcp_retrosynth library.
  - `propose_retrosynthesis_deep` — multi-step route search via
    AiZynthFinder. Cached in external_facts for 30 days; first call
    downloads ~500 MB of demo policy models.

`build_retrosynth_tools(user_id, session_factory)` returns the
`SdkMcpTool` list. `user_id` is needed for the external_facts
cache audit field; `session_factory` opens DB sessions for the cache
read/write.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import UTC
from typing import Any

from claude_agent_sdk import SdkMcpTool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.agent.tool_adapter import wrap_tool
from api.agent.tool_helpers import _cache_is_fresh, _parse_cached_payload

logger = logging.getLogger(__name__)


def build_retrosynth_tools(
    user_id: str,
    session_factory: async_sessionmaker[AsyncSession],
) -> list[SdkMcpTool[Any]]:
    """Build the single-step + deep retrosynthesis tools."""

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

    async def propose_retrosynthesis_deep(
        target_smiles: str,
        max_routes: int = 5,
        max_seconds: int = 300,
    ) -> dict[str, Any]:
        """Multi-step retrosynthesis search via AiZynthFinder.

        Complements `propose_retrosynthesis` (the 11-template single-step
        library). Use for full route discovery on a confirmed target;
        use the fast single-step tool for first-pass disconnection
        enumeration.

        Behaviour:
          - Requires `[retrosynth]` extras (`pip install -e .[retrosynth]`
            on the worker). When absent: returns
            `{"error": "[retrosynth] extras not installed"}` cleanly.
          - First call downloads ~500 MB of demo policy + filter models
            into AiZynthFinder's cache dir. Subsequent calls reuse them.
            Operators can point at the full USPTO bundle via
            `AIZYNTH_CONFIG_PATH`.
          - Wall-cap at `max_seconds` (default 300, 1–600 allowed).
            Tree search is sync; we offload to a thread pool so the
            event loop stays responsive.
          - Result cached in `external_facts` keyed by
            `aizynth:<smiles>` for 30 days.

        Returns:
            {target, routes: [...], total, model, cached: bool} or
            {error}. Each route is a nested AiZynthFinder reaction tree
            (smiles, type, children, in_stock, …).
        """
        from datetime import datetime as _dt
        from datetime import timedelta

        from api.db.queries.knowledge import (
            get_external_fact_by_source_id,
            upsert_external_fact,
        )

        s = target_smiles.strip()
        if not s or len(s) > 1000:
            return {"error": "target_smiles must be 1-1000 chars"}
        if not (1 <= max_routes <= 20):
            return {"error": "max_routes must be between 1 and 20"}
        if not (1 <= max_seconds <= 600):
            return {"error": "max_seconds must be between 1 and 600"}

        cache_key = f"aizynth:{s}"
        cutoff = _dt.now(tz=UTC) - timedelta(days=30)
        async with session_factory() as db:
            cached = await get_external_fact_by_source_id(db, cache_key)
        if cached and _cache_is_fresh(cached.get("last_seen"), cutoff):
            payload = _parse_cached_payload(cached.get("payload"), cache_key=cache_key)
            if "routes" in payload:
                return {**payload, "cached": True}

        try:
            from api.agent.retrosynth_deep import run_deep_retrosynthesis
        except ImportError:
            return {
                "error": (
                    "[retrosynth] extras not installed — run "
                    "`pip install chemclaw2-backend[retrosynth]` "
                    "on this worker to enable deep retrosynthesis"
                ),
            }

        try:
            result = await asyncio.wait_for(
                asyncio.to_thread(run_deep_retrosynthesis, s, max_routes),
                timeout=float(max_seconds),
            )
        except TimeoutError:
            return {
                "error": f"aizynthfinder timed out after {max_seconds}s",
                "target": s,
            }
        except ValueError as e:
            return {"error": str(e)}
        except Exception:
            logger.exception("aizynthfinder run failed smiles_len=%d", len(s))
            return {"error": "deep retrosynthesis failed; see worker logs"}

        async with session_factory() as db:
            await upsert_external_fact(
                db, "aizynth", cache_key,
                result,
                f"deep retrosynthesis for {s} ({result.get('total', 0)} routes)",
                fetched_by=user_id,
            )
        return {**result, "cached": False}

    return [
        wrap_tool("propose_retrosynthesis", propose_retrosynthesis),
        wrap_tool("propose_retrosynthesis_deep", propose_retrosynthesis_deep),
    ]
