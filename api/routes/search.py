"""Search routes — GET /api/search (FTS), POST /api/search (fingerprint similarity)."""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_optional_user
from api.db.connection import get_db
from api.db.queries.rate_limit import pg_rate_limit
from api.db.queries.wiki import search_wiki_by_fts

router = APIRouter()

_FP_RE = re.compile(r'^[01]{2048}$')


class FingerprintSearchRequest(BaseModel):
    fingerprint_bits: str | None = None
    rxn_fingerprint_bits: str | None = None
    limit: int = Field(default=20, ge=1, le=200)
    min_score: float = Field(default=0.4, ge=0.0, le=1.0)


@router.get("/api/search")
async def search_get(
    q: str = Query(..., min_length=1, max_length=500),
    limit: int = Query(20, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
    user_id: str | None = Depends(get_optional_user),
):
    limited = await pg_rate_limit(db, f"search:{user_id or 'anon'}", 30, 60_000)
    if limited["limited"]:
        raise HTTPException(status_code=429, detail="Too many requests")
    results = await search_wiki_by_fts(db, q, limit=limit)
    return {"query": q, "wiki": results}


@router.post("/api/search")
async def search_post(
    body: FingerprintSearchRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str | None = Depends(get_optional_user),
):
    limited = await pg_rate_limit(db, f"search:{user_id or 'anon'}", 30, 60_000)
    if limited["limited"]:
        raise HTTPException(status_code=429, detail="Too many requests")

    if body.fingerprint_bits:
        if not _FP_RE.match(body.fingerprint_bits):
            raise HTTPException(status_code=400, detail="fingerprint_bits must be exactly 2048 binary characters")
        from api.db.queries.compounds import find_similar_compounds
        results = await find_similar_compounds(
            db, body.fingerprint_bits, body.limit, body.min_score
        )
        return {"type": "compound_similarity", "results": results}

    if body.rxn_fingerprint_bits:
        if not _FP_RE.match(body.rxn_fingerprint_bits):
            raise HTTPException(status_code=400, detail="rxn_fingerprint_bits must be exactly 2048 binary characters")
        from api.db.queries.reactions import find_similar_reactions
        results = await find_similar_reactions(
            db, body.rxn_fingerprint_bits, body.limit, body.min_score
        )
        return {"type": "reaction_similarity", "results": results}

    raise HTTPException(status_code=400, detail="Provide fingerprint_bits or rxn_fingerprint_bits")
