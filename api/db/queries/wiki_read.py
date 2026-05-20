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
    import asyncio

    safe_limit = min(max(1, limit), 50)
    # Over-fetch from each leg so RRF has enough candidates to fuse.
    leg_limit = safe_limit * 3
    fts_rows, sem_rows = await asyncio.gather(
        search_wiki_by_fts(db, query, limit=leg_limit, include_archived=include_archived),
        semantic_search_wiki(
            db, embedding, limit=leg_limit, include_archived=include_archived
        ),
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
        {"uid": user_id, "lim": min(max(1, limit), 200)},
    )
    return [dict(r._mapping) for r in result]


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
            WHERE page_id = CAST(:page_id AS uuid)
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
            WHERE page_id = CAST(:pid AS uuid)
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
            WHERE page_id = CAST(:pid AS uuid) AND version = :v
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
    """Return the page state at `as_of` (bi-temporal lookup).

    The response always carries two extra fields callers can surface to UI
    or audit:

    - `temporal_exact: bool` — True iff the returned row's `updated_at` is
      exactly the requested `as_of` (rare; usually False because as_of is
      arbitrary).
    - `temporal_warning: str | None` — human-readable note when the
      returned content does NOT correspond to the requested moment. The
      compliance §3.8 reproducibility story needs this signal so a stale
      result isn't silently presented as authoritative.

    Cases:
      1. Current row's updated_at <= as_of: return it. exact iff equal.
      2. Page exists but was updated after as_of and a matching revision
         is found: return that revision blended with page-level fields.
         exact iff revision.updated_at == as_of.
      3. Page row predates as_of but no revision is older than as_of:
         return the earliest revision with a warning that the response
         post-dates the requested moment.
    """
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
        return {
            **page,
            "temporal_exact": page["updated_at"] == as_of,
            "temporal_warning": None,
        }
    # Page exists but was updated after as_of — look in revisions for the version
    # whose updated_at is closest to but not after as_of.
    rev = await db.execute(
        text("""
            SELECT id::text, page_id::text, version, title, content, content_text,
                   updated_by, updated_at
            FROM wiki_revisions
            WHERE page_id = CAST(:pid AS uuid) AND updated_at <= :as_of
            ORDER BY updated_at DESC
            LIMIT 1
        """),
        {"pid": page["id"], "as_of": as_of},
    )
    rev_row = rev.one_or_none()
    if not rev_row:
        # The page was created before as_of, but no revision is older than
        # as_of — return the earliest known version and flag the gap.
        earliest = await db.execute(
            text("""
                SELECT id::text, page_id::text, version, title, content, content_text,
                       updated_by, updated_at
                FROM wiki_revisions
                WHERE page_id = CAST(:pid AS uuid)
                ORDER BY version ASC
                LIMIT 1
            """),
            {"pid": page["id"]},
        )
        earliest_row = earliest.one_or_none()
        if not earliest_row:
            return None
        rev_dict = dict(earliest_row._mapping)
        return {
            **page,
            **rev_dict,
            "temporal_exact": False,
            "temporal_warning": (
                "No revision older than the requested timestamp exists; "
                "returning the earliest available revision which post-dates as_of."
            ),
        }
    rev_dict = dict(rev_row._mapping)
    return {
        **page,
        **rev_dict,
        "temporal_exact": rev_dict["updated_at"] == as_of,
        "temporal_warning": None,
    }
