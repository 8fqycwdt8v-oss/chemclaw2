"""Reaction queries — Python port of packages/db/src/queries/reactions.ts."""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import Reaction
from api.db.queries.fp_utils import bit_string_to_pg_bytes, rerank_by_tanimoto


async def find_similar_reactions(
    db: AsyncSession,
    query_fp_bits: str,
    limit: int = 20,
    min_similarity: float = 0.4,
    include_outcomes: bool = False,
) -> list[dict[str, Any]]:
    """Similarity search over the reactions table by DRFP bit vector.

    When ``include_outcomes=True`` each returned row gains an ``outcomes``
    list (newest first) sourced from ``reaction_outcomes``. The join runs
    only for the reactions that survive the Tanimoto rerank, so the cost
    scales with ``limit``, not the full HNSW candidate pool.
    """
    safe_limit = max(1, min(limit, 100))
    safe_min = max(0.0, min(min_similarity, 1.0))

    # bit_string_to_pg_bytes packs the 2048-char 0/1 string into 256 bytes.
    # asyncpg's binary protocol rejects str for bit(2048) param binds even
    # with CAST(); bytes pass through cleanly.
    result = await db.execute(
        text("""
            SELECT id::text, rxn_smiles, name, conditions, drfp::text AS fp
            FROM reactions
            WHERE drfp IS NOT NULL
            ORDER BY drfp <~> CAST(:bits AS bit(2048))
            LIMIT 100
        """),
        {"bits": bit_string_to_pg_bytes(query_fp_bits)},
    )
    rows = [dict(r._mapping) for r in result]
    ranked = rerank_by_tanimoto(rows, query_fp_bits, safe_min, safe_limit)

    outcomes_by_reaction: dict[str, list[dict[str, Any]]] = {}
    if include_outcomes and ranked:
        ids = [r["id"] for r in ranked]
        out_result = await db.execute(
            text("""
                SELECT reaction_id::text AS reaction_id,
                       id::text AS id,
                       source, status, yield_pct,
                       conditions_actual, observations, failure_reason,
                       recorded_at
                FROM reaction_outcomes
                WHERE reaction_id = ANY(CAST(:ids AS uuid[]))
                ORDER BY recorded_at DESC
            """),
            {"ids": ids},
        )
        for row in out_result:
            d = dict(row._mapping)
            rid = d.pop("reaction_id")
            if d.get("yield_pct") is not None:
                d["yield_pct"] = float(d["yield_pct"])
            outcomes_by_reaction.setdefault(rid, []).append(d)

    out: list[dict[str, Any]] = []
    for r in ranked:
        entry: dict[str, Any] = {
            "id": r["id"],
            "rxnSmiles": r["rxn_smiles"],
            "name": r["name"],
            "conditions": r["conditions"],
            "similarity": r["similarity"],
        }
        if include_outcomes:
            entry["outcomes"] = outcomes_by_reaction.get(r["id"], [])
        out.append(entry)
    return out


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
