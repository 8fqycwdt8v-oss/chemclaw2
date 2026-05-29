"""Compound queries — Python port of packages/db/src/queries/compounds.ts."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.models import Compound
from api.db.queries._helpers import clamp_limit, rows_to_dicts
from api.db.queries.fp_utils import bit_string_to_pg_bytes, rerank_by_tanimoto


async def count_pending_fingerprints(db: AsyncSession) -> dict[str, int]:
    result = await db.execute(text("""
        SELECT
          (SELECT count(*)::int FROM compounds WHERE morgan_fp IS NULL) AS pending_compounds,
          (SELECT count(*)::int FROM reactions  WHERE drfp      IS NULL) AS pending_reactions
    """))
    row = result.one()
    return {"pending_compounds": row.pending_compounds, "pending_reactions": row.pending_reactions}


async def known_cas_numbers(db: AsyncSession, cas_numbers: list[str]) -> set[str]:
    if not cas_numbers:
        return set()
    result = await db.execute(
        select(Compound.cas_number).where(Compound.cas_number.in_(cas_numbers))
    )
    return {r for (r,) in result if r is not None}


async def find_similar_compounds(
    db: AsyncSession,
    query_fp_bits: str,
    limit: int = 20,
    min_tanimoto: float = 0.4,
    created_after: str | None = None,
    has_cas: bool = False,
) -> list[dict[str, Any]]:
    safe_limit = clamp_limit(limit, 100)
    safe_min = max(0.0, min(min_tanimoto, 1.0))

    where_clauses = ["morgan_fp IS NOT NULL"]
    params: dict[str, Any] = {"bits": bit_string_to_pg_bytes(query_fp_bits)}
    if created_after:
        dt = datetime.fromisoformat(created_after)
        where_clauses.append("created_at >= :created_after")
        params["created_after"] = dt
    if has_cas:
        where_clauses.append("cas_number IS NOT NULL")

    where = " AND ".join(where_clauses)
    result = await db.execute(
        text(f"""
            SELECT id::text, smiles, canon_smiles, name, cas_number,
                   morgan_fp::text AS fp
            FROM compounds
            WHERE {where}
            -- `<%>` = Jaccard distance (1 - Tanimoto), matching the
            -- bit_jaccard_ops HNSW index (migrations 0046) and the Tanimoto
            -- rerank below. Hamming (`<~>`) pruned by the wrong metric.
            ORDER BY morgan_fp <%> CAST(:bits AS bit(2048))
            LIMIT 100
        """),
        params,
    )
    rows = rows_to_dicts(result)
    ranked = rerank_by_tanimoto(rows, query_fp_bits, safe_min, safe_limit)
    return [
        {
            "id": r["id"],
            "smiles": r["smiles"],
            "canonSmiles": r["canon_smiles"],
            "name": r["name"],
            "casNumber": r["cas_number"],
            "tanimoto": r["similarity"],
        }
        for r in ranked
    ]


async def list_compounds_for_substructure(
    db: AsyncSession,
    max_candidates: int = 1000,
) -> list[dict[str, Any]]:
    limit = min(max_candidates, 5000)
    result = await db.execute(
        text("SELECT id::text, smiles, canon_smiles, name, cas_number FROM compounds LIMIT :lim"),
        {"lim": limit},
    )
    return [
        {
            "id": r.id,
            "smiles": r.smiles,
            "canonSmiles": r.canon_smiles,
            "name": r.name,
            "casNumber": r.cas_number,
        }
        for r in result
    ]


async def insert_compound(
    db: AsyncSession,
    smiles: str,
    created_by: str,
    name: str | None = None,
    cas_number: str | None = None,
) -> str:
    compound = Compound(
        id=uuid.uuid4(),
        smiles=smiles,
        created_by=created_by,
        name=name,
        cas_number=cas_number,
    )
    db.add(compound)
    await db.flush()
    return str(compound.id)
