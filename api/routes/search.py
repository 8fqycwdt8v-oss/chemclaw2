"""Search routes — GET /api/search (FTS/hybrid), POST /api/search (fingerprint)."""
from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.connection import get_db
from api.db.queries.rate_limit import rate_limit
from api.db.queries.wiki_read import hybrid_search_wiki, search_wiki_by_fts

router = APIRouter()

_RL_SEARCH = Depends(rate_limit("search", 30, optional_user=True))

# A folded fingerprint is exactly 2048 binary characters; reject anything
# else at the Pydantic boundary (422) rather than hand-rolling the check.
_FP_PATTERN = r'^[01]{2048}$'


class FingerprintSearchRequest(BaseModel):
    fingerprint_bits: str | None = Field(default=None, pattern=_FP_PATTERN)
    rxn_fingerprint_bits: str | None = Field(default=None, pattern=_FP_PATTERN)
    limit: int = Field(default=20, ge=1, le=200)
    min_score: float = Field(default=0.4, ge=0.0, le=1.0)


@router.get("/api/search", dependencies=[_RL_SEARCH])
async def search_get(
    q: str = Query(..., min_length=1, max_length=500),
    limit: int = Query(20, ge=1, le=200),
    mode: Literal["fts", "hybrid"] = Query(
        "hybrid",
        description=(
            "'hybrid' (default) fuses FTS + pgvector semantic search via "
            "Reciprocal Rank Fusion. 'fts' is the legacy text-only path; "
            "use it for exact-term queries (SMILES, CAS) where you don't "
            "want semantic neighbours diluting the result."
        ),
    ),
    db: AsyncSession = Depends(get_db),
):
    if mode == "hybrid":
        # Defer the embedding cost until we're actually using it.
        from api.embeddings import embed_texts
        embeddings = await embed_texts([q])
        results = await hybrid_search_wiki(db, q, embeddings[0], limit=limit)
    else:
        results = await search_wiki_by_fts(db, q, limit=limit)
    return {"query": q, "mode": mode, "wiki": results}


@router.post("/api/search", dependencies=[_RL_SEARCH])
async def search_post(
    body: FingerprintSearchRequest,
    db: AsyncSession = Depends(get_db),
):
    if body.fingerprint_bits:
        from api.db.queries.compounds import find_similar_compounds
        results = await find_similar_compounds(
            db, body.fingerprint_bits, body.limit, body.min_score
        )
        return {"type": "compound_similarity", "results": results}

    if body.rxn_fingerprint_bits:
        from api.db.queries.reactions import find_similar_reactions
        results = await find_similar_reactions(
            db, body.rxn_fingerprint_bits, body.limit, body.min_score
        )
        return {"type": "reaction_similarity", "results": results}

    raise HTTPException(status_code=400, detail="Provide fingerprint_bits or rxn_fingerprint_bits")
