"""Read-side wiki queries.

Split from the original api/db/queries/wiki.py (was 521 LOC) — see C1 in
BACKLOG.md. Pair with wiki_write.py (upsert / patch / chunking).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.embeddings import EMBED_DIM


# ── list / search ─────────────────────────────────────────────────────────────

async def search_wiki_by_fts(
    db: AsyncSession,
    query: str,
    limit: int = 20,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    archived_clause = "" if include_archived else "AND archived = false"
    result = await db.execute(
        text(f"""
            SELECT id::text, slug, title, content_text, maturity
            FROM wiki_pages
            WHERE to_tsvector('english', coalesce(content_text, ''))
                  @@ plainto_tsquery('english', :q)
                  {archived_clause}
            LIMIT :lim
        """),
        {"q": query, "lim": min(limit, 200)},
    )
    return [dict(r._mapping) for r in result]


async def semantic_search_wiki(
    db: AsyncSession,
    embedding: list[float],
    limit: int = 5,
    max_distance: float = 0.5,
    max_chunks_per_page: int = 2,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    if len(embedding) != EMBED_DIM:
        raise ValueError(f"embedding must have {EMBED_DIM} dimensions, got {len(embedding)}")
    safe_limit = min(max(1, limit), 50)
    archived_clause = "" if include_archived else "AND p.archived = false"
    vec_str = "[" + ",".join(map(str, embedding)) + "]"
    result = await db.execute(
        text(f"""
            SELECT c.page_id::text, p.slug, p.title, p.maturity, c.text,
                   (c.embedding <=> :vec::vector({EMBED_DIM})) AS distance
            FROM wiki_chunks c
            JOIN wiki_pages p ON p.id = c.page_id
            WHERE c.embedding IS NOT NULL
                  {archived_clause}
              AND (c.embedding <=> :vec::vector({EMBED_DIM})) < :max_dist
            ORDER BY distance
            LIMIT :pre_limit
        """),
        {"vec": vec_str, "max_dist": max_distance, "pre_limit": safe_limit * 4},
    )
    rows = [dict(r._mapping) for r in result]

    # Per-page cap
    seen: dict[str, int] = {}
    out = []
    for row in rows:
        pid = row["page_id"]
        if seen.get(pid, 0) >= max_chunks_per_page:
            continue
        seen[pid] = seen.get(pid, 0) + 1
        out.append(row)
        if len(out) >= safe_limit:
            break
    return out


async def list_wiki_pages(
    db: AsyncSession,
    page_size: int = 50,
    cursor_updated_at: datetime | None = None,
    cursor_id: str | None = None,
    project: str | None = None,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    clauses = []
    params: dict[str, Any] = {"lim": min(page_size, 200)}
    if not include_archived:
        clauses.append("archived = false")
    if project:
        clauses.append("project = :project")
        params["project"] = project
    if cursor_updated_at and cursor_id:
        clauses.append("(updated_at, id) < (:cur_ts, :cur_id::uuid)")
        params["cur_ts"] = cursor_updated_at
        params["cur_id"] = cursor_id
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    result = await db.execute(
        text(f"""
            SELECT id::text, slug, title, content_text, maturity, project,
                   updated_at, archived, needs_review
            FROM wiki_pages
            {where}
            ORDER BY updated_at DESC, id DESC
            LIMIT :lim
        """),
        params,
    )
    return [dict(r._mapping) for r in result]


async def list_wiki_projects(db: AsyncSession) -> list[str]:
    result = await db.execute(
        text("SELECT DISTINCT project FROM wiki_pages WHERE project IS NOT NULL ORDER BY project")
    )
    return [r.project for r in result]


async def get_wiki_page(
    db: AsyncSession,
    slug: str,
    include_archived: bool = False,
) -> dict[str, Any] | None:
    archived_clause = "" if include_archived else "AND archived = false"
    result = await db.execute(
        text(f"""
            SELECT id::text, slug, title, content, content_text, maturity, project,
                   created_by, updated_by, created_at, updated_at, version,
                   needs_review, archived, valid_from, valid_to
            FROM wiki_pages
            WHERE slug = :slug {archived_clause}
        """),
        {"slug": slug},
    )
    row = result.one_or_none()
    return dict(row._mapping) if row else None


async def get_wiki_page_citations(db: AsyncSession, page_id: str) -> list[dict[str, Any]]:
    result = await db.execute(
        text("""
            SELECT id::text, citation_id, source_type, source_id, label, disputed
            FROM wiki_citations
            WHERE page_id = :page_id::uuid
        """),
        {"page_id": page_id},
    )
    return [dict(r._mapping) for r in result]


# ── revisions ─────────────────────────────────────────────────────────────────

async def list_wiki_revisions(
    db: AsyncSession,
    page_id: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    result = await db.execute(
        text("""
            SELECT id::text, page_id::text, version, title, updated_by, updated_at
            FROM wiki_revisions
            WHERE page_id = :pid::uuid
            ORDER BY version DESC
            LIMIT :lim
        """),
        {"pid": page_id, "lim": min(limit, 50)},
    )
    return [dict(r._mapping) for r in result]


async def get_wiki_revision(
    db: AsyncSession,
    page_id: str,
    version: int,
) -> dict[str, Any] | None:
    result = await db.execute(
        text("""
            SELECT id::text, page_id::text, version, title, content, content_text,
                   updated_by, updated_at
            FROM wiki_revisions
            WHERE page_id = :pid::uuid AND version = :v
        """),
        {"pid": page_id, "v": version},
    )
    row = result.one_or_none()
    return dict(row._mapping) if row else None


async def get_wiki_page_at(
    db: AsyncSession,
    slug: str,
    as_of: datetime,
) -> dict[str, Any] | None:
    # Check if the current page was already at or before as_of.
    current = await db.execute(
        text("""
            SELECT id::text, slug, title, content, content_text, maturity, project,
                   created_by, updated_by, created_at, updated_at, version,
                   needs_review, archived
            FROM wiki_pages
            WHERE slug = :slug AND created_at <= :as_of
        """),
        {"slug": slug, "as_of": as_of},
    )
    row = current.one_or_none()
    if not row:
        return None
    page = dict(row._mapping)
    if page["updated_at"] <= as_of:
        return page
    # Page exists but was updated after as_of — look in revisions for the version
    # whose updated_at is closest to but not after as_of.
    rev = await db.execute(
        text("""
            SELECT id::text, page_id::text, version, title, content, content_text,
                   updated_by, updated_at
            FROM wiki_revisions
            WHERE page_id = :pid::uuid AND updated_at <= :as_of
            ORDER BY updated_at DESC
            LIMIT 1
        """),
        {"pid": page["id"], "as_of": as_of},
    )
    rev_row = rev.one_or_none()
    if not rev_row:
        # The page was created after as_of in terms of content, but the page row
        # itself predates it — return the earliest known version.
        earliest = await db.execute(
            text("""
                SELECT id::text, page_id::text, version, title, content, content_text,
                       updated_by, updated_at
                FROM wiki_revisions
                WHERE page_id = :pid::uuid
                ORDER BY version ASC
                LIMIT 1
            """),
            {"pid": page["id"]},
        )
        earliest_row = earliest.one_or_none()
        return dict(earliest_row._mapping) if earliest_row else None
    return {**page, **dict(rev_row._mapping)}
