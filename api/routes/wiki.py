"""Wiki routes — GET/POST /api/wiki, GET/PATCH /api/wiki/{slug}."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user, get_optional_user
from api.db.connection import get_db
from api.db.queries.contradictions import (
    create_contradiction,
    list_contradictions,
    resolve_contradiction,
)
from api.db.queries.rate_limit import make_key, pg_rate_limit
from api.db.queries.subscriptions import (
    list_subscriptions,
    mark_seen,
    subscribe,
    unsubscribe,
)
from api.db.queries.wiki_read import (
    get_wiki_page,
    get_wiki_page_at,
    get_wiki_page_citations,
    get_wiki_revision,
    list_wiki_pages,
    list_wiki_projects,
    list_wiki_revisions,
    search_wiki_by_fts,
)
from api.db.queries.wiki_write import (
    patch_wiki_page,
    upsert_wiki_page,
)
from api.embeddings import embed_texts

router = APIRouter()
logger = logging.getLogger(__name__)

_SLUG_RE = re.compile(r'^[a-z0-9][a-z0-9-]*[a-z0-9]$')
_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)


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


class WikiSeenBody(BaseModel):
    version: int


class ContradictionBody(BaseModel):
    citation_a: str
    citation_b: str
    proposed_winner: Literal["a", "b", "inconclusive"]
    reason: str


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
    limited = await pg_rate_limit(db, make_key("wiki-read", user_id), 60, 60_000)
    if limited["limited"]:
        raise HTTPException(status_code=429, detail="Too many requests")

    if projects:
        return {"projects": await list_wiki_projects(db)}

    if q:
        if len(q) > 500:
            raise HTTPException(status_code=400, detail="Query too long")
        results = await search_wiki_by_fts(db, q)
        return {"pages": results, "nextCursor": None}

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
    limited = await pg_rate_limit(db, make_key("wiki", user_id), 20, 60_000)
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
        [c.model_dump() for c in (body.citations or [])],
        embed_texts,
        body.project,
        body.needs_review,
    )
    return {"id": page_id}


@router.get("/api/wiki/subscriptions")
async def get_wiki_subscriptions(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    limited = await pg_rate_limit(db, make_key("wiki-read", user_id), 60, 60_000)
    if limited["limited"]:
        raise HTTPException(status_code=429, detail="Too many requests")
    subs = await list_subscriptions(db, user_id)
    return {"subscriptions": subs}


@router.get("/api/wiki/{slug}")
async def get_wiki(
    slug: str,
    as_of: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user_id: str | None = Depends(get_optional_user),
):
    limited = await pg_rate_limit(db, make_key("wiki-read", user_id), 60, 60_000)
    if limited["limited"]:
        raise HTTPException(status_code=429, detail="Too many requests")
    if as_of:
        try:
            as_of_dt = datetime.fromisoformat(as_of)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid asOf timestamp")
        page = await get_wiki_page_at(db, slug, as_of_dt)
        if not page:
            raise HTTPException(status_code=404, detail=f"Wiki page '{slug}' not found at {as_of}")
        return page
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
    limited = await pg_rate_limit(db, make_key("wiki", user_id), 20, 60_000)
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
        [c.model_dump() for c in (body.citations or [])],
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
    limited = await pg_rate_limit(db, make_key("wiki", user_id), 20, 60_000)
    if limited["limited"]:
        logger.warning("wiki_patch_rate_limited user=%s", user_id)
        raise HTTPException(status_code=429, detail="Too many requests")
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


# ── Revisions ──────────────────────────────────────────────────────────────────

@router.get("/api/wiki/{slug}/revisions")
async def get_wiki_revisions(
    slug: str,
    limit: int = Query(20, ge=1, le=50),
    db: AsyncSession = Depends(get_db),
    user_id: str | None = Depends(get_optional_user),
):
    limited = await pg_rate_limit(db, make_key("wiki-read", user_id), 60, 60_000)
    if limited["limited"]:
        raise HTTPException(status_code=429, detail="Too many requests")
    page = await get_wiki_page(db, slug, include_archived=True)
    if not page:
        raise HTTPException(status_code=404, detail=f"Wiki page '{slug}' not found")
    revisions = await list_wiki_revisions(db, page["id"], limit=limit)
    return {"revisions": revisions}


@router.get("/api/wiki/{slug}/revisions/{version}")
async def get_wiki_revision_by_version(
    slug: str,
    version: int,
    db: AsyncSession = Depends(get_db),
    user_id: str | None = Depends(get_optional_user),
):
    limited = await pg_rate_limit(db, make_key("wiki-read", user_id), 60, 60_000)
    if limited["limited"]:
        raise HTTPException(status_code=429, detail="Too many requests")
    page = await get_wiki_page(db, slug, include_archived=True)
    if not page:
        raise HTTPException(status_code=404, detail=f"Wiki page '{slug}' not found")
    rev = await get_wiki_revision(db, page["id"], version)
    if not rev:
        raise HTTPException(status_code=404, detail=f"Revision {version} not found for '{slug}'")
    return rev


# ── Subscriptions ──────────────────────────────────────────────────────────────

@router.post("/api/wiki/{slug}/subscribe")
async def subscribe_wiki(
    slug: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    limited = await pg_rate_limit(db, make_key("wiki", user_id), 20, 60_000)
    if limited["limited"]:
        raise HTTPException(status_code=429, detail="Too many requests")
    page = await get_wiki_page(db, slug)
    if not page:
        raise HTTPException(status_code=404, detail=f"Wiki page '{slug}' not found")
    await subscribe(db, user_id, page["id"])
    return {"ok": True}


@router.delete("/api/wiki/{slug}/subscribe")
async def unsubscribe_wiki(
    slug: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    limited = await pg_rate_limit(db, make_key("wiki", user_id), 20, 60_000)
    if limited["limited"]:
        raise HTTPException(status_code=429, detail="Too many requests")
    page = await get_wiki_page(db, slug)
    if not page:
        raise HTTPException(status_code=404, detail=f"Wiki page '{slug}' not found")
    await unsubscribe(db, user_id, page["id"])
    return {"ok": True}


@router.post("/api/wiki/{slug}/seen")
async def mark_wiki_seen(
    slug: str,
    body: WikiSeenBody,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    limited = await pg_rate_limit(db, make_key("wiki", user_id), 20, 60_000)
    if limited["limited"]:
        raise HTTPException(status_code=429, detail="Too many requests")
    page = await get_wiki_page(db, slug)
    if not page:
        raise HTTPException(status_code=404, detail=f"Wiki page '{slug}' not found")
    if body.version > page["version"]:
        raise HTTPException(
            status_code=400,
            detail=f"version {body.version} exceeds current page version {page['version']}",
        )
    await mark_seen(db, user_id, page["id"], body.version)
    return {"ok": True}


# ── Contradictions ─────────────────────────────────────────────────────────────

@router.get("/api/wiki/{slug}/contradictions")
async def get_wiki_contradictions(
    slug: str,
    resolved: bool = Query(False),
    db: AsyncSession = Depends(get_db),
    user_id: str | None = Depends(get_optional_user),
):
    limited = await pg_rate_limit(db, make_key("wiki-read", user_id), 60, 60_000)
    if limited["limited"]:
        raise HTTPException(status_code=429, detail="Too many requests")
    page = await get_wiki_page(db, slug, include_archived=True)
    if not page:
        raise HTTPException(status_code=404, detail=f"Wiki page '{slug}' not found")
    items = await list_contradictions(db, page_id=page["id"], resolved=resolved)
    return {"contradictions": items}


@router.post("/api/wiki/{slug}/contradictions")
async def create_wiki_contradiction(
    slug: str,
    body: ContradictionBody,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    limited = await pg_rate_limit(db, make_key("wiki", user_id), 20, 60_000)
    if limited["limited"]:
        raise HTTPException(status_code=429, detail="Too many requests")
    page = await get_wiki_page(db, slug)
    if not page:
        raise HTTPException(status_code=404, detail=f"Wiki page '{slug}' not found")
    contradiction_id = await create_contradiction(
        db, page["id"], body.citation_a, body.citation_b,
        body.proposed_winner, body.reason,
    )
    return {"id": contradiction_id}


@router.patch("/api/wiki/{slug}/contradictions/{contradiction_id}/resolve")
async def resolve_wiki_contradiction(
    slug: str,
    contradiction_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    limited = await pg_rate_limit(db, make_key("wiki", user_id), 20, 60_000)
    if limited["limited"]:
        raise HTTPException(status_code=429, detail="Too many requests")
    page = await get_wiki_page(db, slug, include_archived=True)
    if not page:
        raise HTTPException(status_code=404, detail=f"Wiki page '{slug}' not found")
    found = await resolve_contradiction(db, contradiction_id, user_id, page_id=page["id"])
    if not found:
        raise HTTPException(status_code=404, detail="Contradiction not found or already resolved")
    return {"ok": True}
