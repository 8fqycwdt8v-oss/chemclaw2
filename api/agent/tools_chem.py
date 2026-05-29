"""Chemistry-registry MCP tools split out of api/agent/tools.py.

Five read-only tools wrapping `api/db/queries/{compounds,reactions,
reaction_outcomes}` lookups. They share a single closure dependency
(`session_factory`); none reach for `user_id` / `session_id` since all
operate on owner-agnostic tables (similarity / outcome history /
substructure candidates).

`build_chem_tools(session_factory)` returns a list of `SdkMcpTool` for
`create_sdk_mcp_server(tools=...)`. Tool bodies are written in the
project's standard kwargs-in / raw-dict-out style and wrapped via
`tool_adapter.wrap_tool` to match the SDK contract.
"""
from __future__ import annotations

from typing import Any

from claude_agent_sdk import SdkMcpTool
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.agent.tool_adapter import wrap_tool
from api.agent.tool_validation import is_fingerprint, parse_uuid


def build_chem_tools(
    session_factory: async_sessionmaker[AsyncSession],
) -> list[SdkMcpTool[Any]]:
    """Build the chemistry-registry search tools for the MCP server."""

    async def compound_similarity_search(
        fingerprint_bits: str,
        limit: int = 20,
        min_tanimoto: float = 0.4,
        created_after: str | None = None,
        has_cas: bool = False,
    ) -> dict[str, Any]:
        """Search the compound registry by Morgan fingerprint similarity (Tanimoto ≥ threshold)."""
        from api.db.queries.compounds import find_similar_compounds
        if not is_fingerprint(fingerprint_bits):
            return {"error": "fingerprint_bits must be exactly 2048 binary digits"}
        async with session_factory() as db:
            results = await find_similar_compounds(
                db, fingerprint_bits, limit, min_tanimoto, created_after, has_cas
            )
        return {"type": "compound_similarity", "results": results}

    async def reaction_similarity_search(
        rxn_fingerprint_bits: str,
        limit: int = 20,
        min_similarity: float = 0.4,
        include_outcomes: bool = False,
    ) -> dict[str, Any]:
        """Search the reaction database by DRFP fingerprint similarity.

        Set ``include_outcomes=True`` to attach experimental results
        (yield, status, conditions actually run, failure reasons) to each
        hit — needed by the process-gap-analyst sub-agent when proposing
        what to investigate next for a reaction step.
        """
        from api.db.queries.reactions import find_similar_reactions
        if not is_fingerprint(rxn_fingerprint_bits):
            return {"error": "rxn_fingerprint_bits must be exactly 2048 binary digits"}
        async with session_factory() as db:
            results = await find_similar_reactions(
                db, rxn_fingerprint_bits, limit, min_similarity,
                include_outcomes=include_outcomes,
            )
        return {"type": "reaction_similarity", "results": results}

    async def suggest_conditions_from_neighbors(
        rxn_fingerprint_bits: str,
        limit: int = 10,
        min_similarity: float = 0.4,
    ) -> dict[str, Any]:
        """Aggregate free-text conditions from top-K DRFP neighbors.

        Call this BEFORE invoking a predictor — it is cheaper, grounded in
        the registry's actual reactions, and the returned reaction ids can
        be cited. Compute the DRFP bits with mcp-rxnfp.compute_drfp first.
        """
        from api.db.queries.reactions import find_neighbor_conditions
        if not is_fingerprint(rxn_fingerprint_bits):
            return {"error": "rxn_fingerprint_bits must be exactly 2048 binary digits"}
        async with session_factory() as db:
            neighbors = await find_neighbor_conditions(
                db, rxn_fingerprint_bits, limit, min_similarity
            )
        return {"type": "neighbor_conditions", "neighbors": neighbors}

    async def list_reaction_outcomes(
        reaction_id: str,
        limit: int = 50,
    ) -> dict[str, Any]:
        """List recorded experimental outcomes for a registered reaction.

        Returns the per-attempt history (yield, status, actual conditions,
        observations, failure reason) newest first, sourced from
        ``reaction_outcomes``. Use this when you have a specific reaction
        in the registry and want to see what's already been tried.
        """
        rid = parse_uuid(reaction_id)
        if rid is None:
            return {"error": "reaction_id must be a UUID"}
        from api.db.queries.reaction_outcomes import list_outcomes_for_reaction
        async with session_factory() as db:
            outcomes = await list_outcomes_for_reaction(db, rid, limit=limit)
        return {"reaction_id": rid, "outcomes": outcomes}

    async def substructure_search(
        smarts: str,
        max_candidates: int = 500,
    ) -> dict[str, Any]:
        """Return compound candidates for substructure SMARTS matching (caller runs RDKit match)."""
        from api.db.queries.compounds import list_compounds_for_substructure
        async with session_factory() as db:
            candidates = await list_compounds_for_substructure(db, max_candidates)
        return {"smarts": smarts, "candidates": candidates}

    return [
        wrap_tool("compound_similarity_search", compound_similarity_search),
        wrap_tool("reaction_similarity_search", reaction_similarity_search),
        wrap_tool("suggest_conditions_from_neighbors", suggest_conditions_from_neighbors),
        wrap_tool("list_reaction_outcomes", list_reaction_outcomes),
        wrap_tool("substructure_search", substructure_search),
    ]
