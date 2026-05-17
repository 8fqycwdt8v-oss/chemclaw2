"""Wiki queries — Python port of packages/db/src/queries/wiki*.ts."""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

EMBED_DIM = 1536


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


async def get_wiki_page(db: AsyncSession, slug: str) -> dict[str, Any] | None:
    result = await db.execute(
        text("""
            SELECT id::text, slug, title, content, content_text, maturity, project,
                   created_by, updated_by, created_at, updated_at, version,
                   needs_review, archived, valid_from, valid_to
            FROM wiki_pages
            WHERE slug = :slug
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


# ── upsert ────────────────────────────────────────────────────────────────────

def chunk_text(text_body: str, chunk_size: int = 800, overlap: int = 100) -> list[str]:
    """Split text into overlapping chunks for embedding."""
    if not text_body.strip():
        return []
    chunks = []
    start = 0
    while start < len(text_body):
        end = start + chunk_size
        chunks.append(text_body[start:end])
        if end >= len(text_body):
            break
        start = end - overlap
    return chunks


async def upsert_wiki_page(
    db: AsyncSession,
    slug: str,
    title: str,
    content: Any,
    content_text: str,
    created_by: str,
    citations: list[dict[str, Any]],
    embed_fn: Any,  # async (texts: list[str]) -> list[list[float]]
    project: str | None = None,
    needs_review: bool | None = None,
) -> str:
    # Pre-flight: skip re-embedding if content unchanged
    existing = await db.execute(
        text("SELECT content_text FROM wiki_pages WHERE slug = :slug"),
        {"slug": slug},
    )
    existing_row = existing.one_or_none()
    content_changed = not existing_row or existing_row.content_text != content_text

    chunks: list[str] = []
    embeddings: list[list[float]] = []
    if content_changed:
        chunks = chunk_text(content_text)
        if chunks:
            embeddings = await embed_fn(chunks)
            if len(embeddings) != len(chunks):
                raise ValueError(f"embed_fn returned {len(embeddings)} vectors for {len(chunks)} chunks")

    # Metadata columns
    meta_cols = ""
    meta_vals = ""
    meta_params: dict[str, Any] = {}
    if project is not None:
        meta_cols += ", project"
        meta_vals += ", :project"
        meta_params["project"] = project
    if needs_review is not None:
        meta_cols += ", needs_review"
        meta_vals += ", :needs_review"
        meta_params["needs_review"] = needs_review

    result = await db.execute(
        text(f"""
            INSERT INTO wiki_pages (slug, title, content, content_text, created_by, updated_by{meta_cols})
            VALUES (:slug, :title, :content::jsonb, :content_text, :created_by, :updated_by{meta_vals})
            ON CONFLICT (slug) DO UPDATE SET
                title        = EXCLUDED.title,
                content      = EXCLUDED.content,
                content_text = EXCLUDED.content_text,
                updated_by   = EXCLUDED.updated_by,
                updated_at   = now(),
                version      = wiki_pages.version + 1
                {", project = EXCLUDED.project" if project is not None else ""}
                {", needs_review = EXCLUDED.needs_review" if needs_review is not None else ""}
            RETURNING id::text
        """),
        {
            "slug": slug, "title": title,
            "content": str(content) if not isinstance(content, str) else content,
            "content_text": content_text,
            "created_by": created_by, "updated_by": created_by,
            **meta_params,
        },
    )
    page_id = result.scalar_one()

    if content_changed:
        await db.execute(
            text("DELETE FROM wiki_chunks WHERE page_id = :pid::uuid"),
            {"pid": page_id},
        )
        if chunks:
            for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                vec_str = "[" + ",".join(map(str, emb)) + "]"
                await db.execute(
                    text("""
                        INSERT INTO wiki_chunks (page_id, chunk_idx, text, embedding)
                        VALUES (:page_id::uuid, :idx, :text, :emb::vector)
                    """),
                    {"page_id": page_id, "idx": i, "text": chunk, "emb": vec_str},
                )

    # Always replace citations
    await db.execute(
        text("DELETE FROM wiki_citations WHERE page_id = :pid::uuid"),
        {"pid": page_id},
    )
    for c in citations:
        await db.execute(
            text("""
                INSERT INTO wiki_citations (page_id, citation_id, source_type, source_id, label)
                VALUES (:page_id::uuid, :citation_id, :source_type, :source_id, :label)
            """),
            {
                "page_id": page_id,
                "citation_id": c["citationId"],
                "source_type": c["sourceType"],
                "source_id": c.get("sourceId"),
                "label": c["label"],
            },
        )

    await db.commit()
    return page_id


async def patch_wiki_page(
    db: AsyncSession,
    slug: str,
    updated_by: str,
    needs_review: bool | None = None,
    archived: bool | None = None,
    maturity: str | None = None,
    project: str | None = None,
) -> dict[str, bool]:
    sets = []
    params: dict[str, Any] = {"slug": slug}
    if needs_review is not None:
        sets.append("needs_review = :needs_review")
        params["needs_review"] = needs_review
    if archived is not None:
        sets.append("archived = :archived")
        params["archived"] = archived
    if maturity is not None:
        sets.append("maturity = :maturity")
        params["maturity"] = maturity
    if project is not None:
        sets.append("project = :project")
        params["project"] = project
    if not sets:
        return {"found": False}

    result = await db.execute(
        text(f"UPDATE wiki_pages SET {', '.join(sets)} WHERE slug = :slug RETURNING id"),
        params,
    )
    await db.commit()
    return {"found": result.one_or_none() is not None}
