"""Read-side wiki queries — current-state search, list, and get.

Pair with wiki_write.py (upsert / patch / chunking) and wiki_temporal.py
(revision list, single-version fetch, bi-temporal as-of lookup).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.queries._helpers import clamp_limit, row_to_dict, rows_to_dicts
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
    return rows_to_dicts(result)


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
    safe_limit = clamp_limit(limit, 50)
    archived_clause = "" if include_archived else "AND p.archived = false"
    vec_str = "[" + ",".join(map(str, embedding)) + "]"
    result = await db.execute(
        text(f"""
            SELECT c.page_id::text, p.slug, p.title, p.maturity, c.text,
                   (c.embedding <=> CAST(:vec AS vector({EMBED_DIM}))) AS distance
            FROM wiki_chunks c
            JOIN wiki_pages p ON p.id = c.page_id
            WHERE c.embedding IS NOT NULL
                  {archived_clause}
              AND (c.embedding <=> CAST(:vec AS vector({EMBED_DIM}))) < :max_dist
            ORDER BY distance
            LIMIT :pre_limit
        """),
        {"vec": vec_str, "max_dist": max_distance, "pre_limit": safe_limit * 4},
    )
    rows = rows_to_dicts(result)

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


async def hybrid_search_wiki(
    db: AsyncSession,
    query: str,
    embedding: list[float],
    limit: int = 10,
    include_archived: bool = False,
) -> list[dict[str, Any]]:
    """Run FTS and semantic search in parallel, fuse with Reciprocal Rank Fusion.

    RRF scores each candidate as ``score = sum(1 / (k + rank_i))`` over the
    ranks it received in each list. Default ``k = 60`` matches the original
    paper (Cormack et al., 2009) and the convention used by Elasticsearch
    and pgvector tutorials. The fused list is deduplicated by page slug.

    Returns one row per page, shape:
        {id, slug, title, maturity, content_text, text, score, fts_rank, sem_rank}

    Spec §3.7 ("FTS + semantic fusion"). Use when the caller can't
    predict whether the query benefits from lexical or semantic recall
    (the common case for natural-language wiki questions).
    """
    safe_limit = clamp_limit(limit, 50)
    # Over-fetch from each leg so RRF has enough candidates to fuse.
    leg_limit = safe_limit * 3
    # Sequential — same SQLAlchemy AsyncSession can't safely run two
    # concurrent queries (`IllegalStateChangeError`). Both legs hit the
    # same Postgres connection pool anyway, so gather was theatre.
    # Matches the fix in hybrid_search_paper_chunks.
    fts_rows = await search_wiki_by_fts(
        db, query, limit=leg_limit, include_archived=include_archived,
    )
    sem_rows = await semantic_search_wiki(
        db, embedding, limit=leg_limit, include_archived=include_archived,
    )

    K = 60  # RRF damping constant — standard value.
    # Build a slug → (rank in fts, rank in semantic, row from whichever has it)
    merged: dict[str, dict[str, Any]] = {}
    for rank, row in enumerate(fts_rows, start=1):
        slug = row["slug"]
        entry = merged.setdefault(slug, {"row": row, "fts_rank": None, "sem_rank": None})
        if entry["fts_rank"] is None:
            entry["fts_rank"] = rank
            # FTS rows have content_text but not chunk text; preserve both.
            if "content_text" in row and "content_text" not in entry["row"]:
                entry["row"] = {**entry["row"], "content_text": row["content_text"]}

    for rank, row in enumerate(sem_rows, start=1):
        slug = row["slug"]
        entry = merged.setdefault(slug, {"row": row, "fts_rank": None, "sem_rank": None})
        if entry["sem_rank"] is None:
            entry["sem_rank"] = rank
            # Semantic rows have the chunk text — useful for snippets.
            if "text" in row:
                entry["row"] = {**entry["row"], "text": row["text"]}

    scored: list[dict[str, Any]] = []
    for slug, entry in merged.items():
        score = 0.0
        if entry["fts_rank"] is not None:
            score += 1.0 / (K + entry["fts_rank"])
        if entry["sem_rank"] is not None:
            score += 1.0 / (K + entry["sem_rank"])
        row = entry["row"]
        # The semantic leg returns the row keyed by `page_id`; the FTS leg
        # uses `id`. Surface a single `id` field so API clients don't have
        # to know which leg the row came from.
        normalized_id = row.get("id") or row.get("page_id")
        scored.append({
            **row,
            "id": normalized_id,
            "slug": slug,
            "score": score,
            "fts_rank": entry["fts_rank"],
            "sem_rank": entry["sem_rank"],
        })

    scored.sort(key=lambda r: r["score"], reverse=True)
    return scored[:safe_limit]


async def list_wiki_needs_review(
    db: AsyncSession,
    user_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return wiki pages with `needs_review=true` created by `user_id`.

    Drives the curator inbox. Owner-scoped because uploads + agent-
    authored pages should land in their creator's inbox — anyone can
    edit them once they're public, but the original author owns the
    triage queue.

    Archived pages are excluded; updates within the last 30 days come
    first so freshly-created drafts surface ahead of stale ones.
    """
    result = await db.execute(
        text("""
            SELECT id::text, slug, title, content_text, project,
                   updated_at, maturity, created_by
            FROM wiki_pages
            WHERE needs_review = true
              AND archived = false
              AND created_by = :uid
            ORDER BY updated_at DESC
            LIMIT :lim
        """),
        {"uid": user_id, "lim": clamp_limit(limit, 200)},
    )
    return rows_to_dicts(result)


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
        clauses.append("(updated_at, id) < (:cur_ts, CAST(:cur_id AS uuid))")
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
    return rows_to_dicts(result)


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
    return row_to_dict(row)


async def get_wiki_page_citations(db: AsyncSession, page_id: str) -> list[dict[str, Any]]:
    result = await db.execute(
        text("""
            SELECT id::text, citation_id, source_type, source_id, label, disputed
            FROM wiki_citations
            WHERE page_id = CAST(:page_id AS uuid)
        """),
        {"page_id": page_id},
    )
    return rows_to_dicts(result)


