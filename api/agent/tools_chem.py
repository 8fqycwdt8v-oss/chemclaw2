"""Chemistry-registry @mcp.tool functions split out of api/agent/tools.py.

These five tools all wrap `api/db/queries/{compounds,reactions,
reaction_outcomes}` lookups over the in-house registry. They share a
single closure dependency (`session_factory`), so the registration
function takes that one parameter — no `user_id` / `session_id` is
needed for any of these (all are read-only against owner-agnostic
tables: similarity / outcome history / substructure candidates).

Register via `register_chem_tools(mcp, session_factory)` from
`build_chemclaw_mcp_server`.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def register_chem_tools(
    mcp: Any,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Attach the chemistry-registry search tools to the given MCP server."""

    # ── compound similarity search ───────────────────────────────────────────
    @mcp.tool()
    async def compound_similarity_search(
        fingerprint_bits: str,
        limit: int = 20,
        min_tanimoto: float = 0.4,
        created_after: str | None = None,
        has_cas: bool = False,
    ) -> dict[str, Any]:
        """Search the compound registry by Morgan fingerprint similarity (Tanimoto ≥ threshold)."""
        from api.db.queries.compounds import find_similar_compounds
        if not re.match(r'^[01]{2048}$', fingerprint_bits):
            return {"error": "fingerprint_bits must be exactly 2048 binary digits"}
        async with session_factory() as db:
            results = await find_similar_compounds(
                db, fingerprint_bits, limit, min_tanimoto, created_after, has_cas
            )
        return {"type": "compound_similarity", "results": results}

    # ── reaction similarity search ────────────────────────────────────────────
    @mcp.tool()
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
        if not re.match(r'^[01]{2048}$', rxn_fingerprint_bits):
            return {"error": "rxn_fingerprint_bits must be exactly 2048 binary digits"}
        async with session_factory() as db:
            results = await find_similar_reactions(
                db, rxn_fingerprint_bits, limit, min_similarity,
                include_outcomes=include_outcomes,
            )
        return {"type": "reaction_similarity", "results": results}

    # ── condition precedent from neighbors ────────────────────────────────────
    @mcp.tool()
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
        if not re.match(r'^[01]{2048}$', rxn_fingerprint_bits):
            return {"error": "rxn_fingerprint_bits must be exactly 2048 binary digits"}
        async with session_factory() as db:
            neighbors = await find_neighbor_conditions(
                db, rxn_fingerprint_bits, limit, min_similarity
            )
        return {"type": "neighbor_conditions", "neighbors": neighbors}

    # ── reaction outcomes lookup ──────────────────────────────────────────────
    @mcp.tool()
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
        try:
            rid = str(uuid.UUID(reaction_id.strip()))
        except (ValueError, AttributeError):
            return {"error": "reaction_id must be a UUID"}
        from api.db.queries.reaction_outcomes import list_outcomes_for_reaction
        async with session_factory() as db:
            outcomes = await list_outcomes_for_reaction(db, rid, limit=limit)
        return {"reaction_id": rid, "outcomes": outcomes}

    # ── substructure search ───────────────────────────────────────────────────
    @mcp.tool()
    async def substructure_search(
        smarts: str,
        max_candidates: int = 500,
    ) -> dict[str, Any]:
        """Return compound candidates for substructure SMARTS matching (caller runs RDKit match)."""
        from api.db.queries.compounds import list_compounds_for_substructure
        async with session_factory() as db:
            candidates = await list_compounds_for_substructure(db, max_candidates)
        return {"smarts": smarts, "candidates": candidates}
