"""Wiki routes — GET/POST /api/wiki, GET/PATCH /api/wiki/{slug}."""
from __future__ import annotations

import os
import re
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from openai import AsyncOpenAI
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, get_optional_user
from api.db.connection import get_db
from api.db.queries.rate_limit import pg_rate_limit
from api.db.queries.wiki import (
    get_wiki_page,
    get_wiki_page_citations,
    list_wiki_pages,
    list_wiki_projects,
    patch_wiki_page,
    search_wiki_by_fts,
    upsert_wiki_page,
)

router = APIRouter()

_SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9-]*[a-z0-9]$')
_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)

_oai_client: AsyncOpenAI | None = None


def _get_oai() -> AsyncOpenAI:
    global _oai_client
    if _oai_client is None:
        _oai_client = AsyncOpenAI(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            base_url=os.environ.get("OPENAI_BASE_URL") or None,
        )
    return _oai_client


async def embed_texts(texts: list[str]) -> list[list[float]]:
    resp = await _get_oai().embeddings.create(model="text-embedding-3-small", input=texts)
    return [d.embedding for d in resp.data]


class CitationIn(BaseModel):
    citation_id: str
    source_type: str
    source_id: str | None = None
    label: str


class WikiPostBody(BaseModel):
    slug: str
    title: str
    content: Any = None
    content_text: str | None = None
    citations: list[CitationIn] | None = None
    project: str | None = None
    needs_review: bool | None = None


class WikiPutBody(BaseModel):
    title: str
    content: Any = None
    content_text: str | None = None
    citations: list[CitationIn] | None = None
    project: str | None = None
    needs_review: bool | None = None


class WikiPatchBody(BaseModel):
    needs_review: bool | None = None
    archived: bool | None = None
    maturity: str | None = None
    project: str | None = None


@router.get("/api/wiki")
async def list_wiki(
    q: str | None = Query(None),
    cursor: str | None = Query(None),
    project: str | None = Query(None),
    include_archived: bool = Query(False),
    projects: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    user_id: str | None = Depends(get_optional_user),
):
    limited = await pg_rate_limit(db, f"wiki-read:{user_id or 'anon'}", 60, 60_000)
    if limited["limited"]:
        raise HTTPException(status_code=429, detail="Too many requests")

    if projects:
        return {"projects": await list_wiki_projects(db)}

    if q:
        if len(q) > 500:
            raise HTTPException(status_code=400, detail="Query too long")
        return await search_wiki_by_fts(db, q)

    cursor_updated_at: datetime | None = None
    cursor_id: str | None = None
    if cursor:
        sep = cursor.rfind('_')
        if sep == -1:
            raise HTTPException(status_code=400, detail="Invalid cursor")
        try:
            cursor_updated_at = datetime.fromisoformat(cursor[:sep])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid cursor")
        cursor_id = cursor[sep + 1:]
        if not _UUID_RE.match(cursor_id):
            raise HTTPException(status_code=400, detail="Invalid cursor")

    pages = await list_wiki_pages(
        db, 50, cursor_updated_at, cursor_id, project, include_archived
    )
    last = pages[-1] if len(pages) == 50 else None
    next_cursor = None
    if last:
        ts = last["updated_at"]
        if hasattr(ts, 'isoformat'):
            ts = ts.isoformat()
        next_cursor = f"{ts}_{last['id']}"
    return {"pages": pages, "nextCursor": next_cursor}


@router.post("/api/wiki")
async def create_wiki(
    body: WikiPostBody,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    limited = await pg_rate_limit(db, f"wiki:{user_id}", 20, 60_000)
    if limited["limited"]:
        raise HTTPException(status_code=429, detail="Too many requests")
    if not _SLUG_RE.match(body.slug):
        raise HTTPException(status_code=400, detail="Invalid slug: use lowercase letters, numbers, and hyphens only")
    existing = await get_wiki_page(db, body.slug, include_archived=True)
    if existing and existing["created_by"] != user_id:
        raise HTTPException(status_code=403, detail="A page with this slug already exists and belongs to another user")
    page_id = await upsert_wiki_page(
        db,
        body.slug,
        body.title,
        body.content or {"type": "doc", "content": []},
        body.content_text or "",
        user_id,
        body.citations or [],
        embed_texts,
        body.project,
        body.needs_review,
    )
    return {"id": page_id}


@router.get("/api/wiki/{slug}")
async def get_wiki(
    slug: str,
    db: AsyncSession = Depends(get_db),
    user_id: str | None = Depends(get_optional_user),
):
    page = await get_wiki_page(db, slug)
    if not page:
        raise HTTPException(status_code=404, detail=f"Wiki page '{slug}' not found")
    citations = await get_wiki_page_citations(db, page["id"])
    return {**page, "citations": citations}


@router.put("/api/wiki/{slug}")
async def update_wiki(
    slug: str,
    body: WikiPutBody,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """Replace a wiki page's content, title, and citations. Triggers re-embed."""
    limited = await pg_rate_limit(db, f"wiki:{user_id}", 20, 60_000)
    if limited["limited"]:
        raise HTTPException(status_code=429, detail="Too many requests")
    existing = await get_wiki_page(db, slug, include_archived=True)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Wiki page '{slug}' not found")
    if existing["created_by"] != user_id:
        raise HTTPException(status_code=403, detail="You can only edit your own wiki pages")
    page_id = await upsert_wiki_page(
        db,
        slug,
        body.title,
        body.content or {"type": "doc", "content": []},
        body.content_text or "",
        user_id,
        body.citations or [],
        embed_texts,
        body.project,
        body.needs_review,
    )
    return {"id": page_id}


@router.patch("/api/wiki/{slug}")
async def patch_wiki(
    slug: str,
    body: WikiPatchBody,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    existing = await get_wiki_page(db, slug, include_archived=True)
    if not existing:
        raise HTTPException(status_code=404, detail=f"Wiki page '{slug}' not found")
    if existing["created_by"] != user_id:
        raise HTTPException(status_code=403, detail="You can only edit your own wiki pages")
    result = await patch_wiki_page(
        db, slug, user_id,
        body.needs_review, body.archived, body.maturity, body.project,
    )
    if not result["found"]:
        raise HTTPException(status_code=404, detail=f"Wiki page '{slug}' not found")
    return {"ok": True}
