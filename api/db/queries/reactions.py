"""Reaction queries — Python port of packages/db/src/queries/reactions.ts."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import Reaction
from api.db.queries.fp_utils import rerank_by_tanimoto, validate_fp_bits


async def find_similar_reactions(
    db: AsyncSession,
    query_fp_bits: str,
    limit: int = 20,
    min_similarity: float = 0.4,
) -> list[dict[str, Any]]:
    validate_fp_bits(query_fp_bits)
    safe_limit = max(1, min(limit, 100))
    safe_min = max(0.0, min(min_similarity, 1.0))

    result = await db.execute(
        text("""
            SELECT id::text, rxn_smiles, name, conditions, drfp::text AS fp
            FROM reactions
            WHERE drfp IS NOT NULL
            ORDER BY drfp <~> CAST(:bits AS bit(2048))
            LIMIT 100
        """),
        {"bits": query_fp_bits},
    )
    rows = [dict(r._mapping) for r in result]
    ranked = rerank_by_tanimoto(rows, query_fp_bits, safe_min, safe_limit)
    return [
        {
            "id": r["id"],
            "rxnSmiles": r["rxn_smiles"],
            "name": r["name"],
            "conditions": r["conditions"],
            "similarity": r["similarity"],
        }
        for r in ranked
    ]


async def find_neighbor_conditions(
    db: AsyncSession,
    query_fp_bits: str,
    limit: int = 10,
    min_similarity: float = 0.4,
) -> list[dict[str, Any]]:
    """Return DRFP neighbors that have a non-empty `conditions` text.

    Wraps `find_similar_reactions` and filters out rows where the
    historical conditions field is null or whitespace — those neighbors
    carry no precedent worth surfacing to the agent.
    """
    neighbors = await find_similar_reactions(db, query_fp_bits, limit, min_similarity)
    return [
        {
            "reactionId": n["id"],
            "rxnSmiles": n["rxnSmiles"],
            "name": n["name"],
            "conditions": n["conditions"],
            "similarity": n["similarity"],
        }
        for n in neighbors
        if n.get("conditions") and n["conditions"].strip()
    ]


async def insert_reaction(
    db: AsyncSession,
    rxn_smiles: str,
    created_by: str,
    name: str | None = None,
    conditions: str | None = None,
) -> str:
    reaction = Reaction(
        id=uuid.uuid4(),
        rxn_smiles=rxn_smiles,
        created_by=created_by,
        name=name,
        conditions=conditions,
    )
    db.add(reaction)
    await db.flush()
    return str(reaction.id)
