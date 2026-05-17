"""Wiki queries — Python port of packages/db/src/queries/wiki*.ts."""
from __future__ import annotations

import json
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


# ── upsert ────────────────────────────────────────────────────────────────────

_MIN_CHUNK_LENGTH = 50


def _split_sentences(text: str) -> list[str]:
    """Split on sentence-ending punctuation followed by whitespace."""
    import re
    parts = re.split(r'(?<=[.!?])\s+', text)
    return [p.strip() for p in parts if p.strip()]


def _split_words(text: str, max_size: int) -> list[str]:
    """Greedy word-boundary split that never exceeds max_size."""
    words = text.split()
    chunks: list[str] = []
    current = ""
    for word in words:
        candidate = (current + " " + word).strip() if current else word
        if len(candidate) > max_size and current:
            chunks.append(current)
            current = word
        else:
            current = candidate
    if current:
        chunks.append(current)
    return chunks


def chunk_text(text_body: str, chunk_size: int = 400, overlap: int = 80) -> list[str]:
    """Split text into overlapping semantic chunks for embedding.

    Strategy (ported from TypeScript packages/db/src/queries/wiki.ts):
    1. Split by paragraph boundaries (double newline).
    2. If a paragraph exceeds chunk_size, split it by sentences.
    3. If a sentence still exceeds chunk_size, split it by words.
    4. Merge short segments into the previous chunk with overlap up to chunk_size.
    5. Drop chunks shorter than _MIN_CHUNK_LENGTH chars.

    Defaults (400 chars, 80 overlap) match the TypeScript implementation.
    """
    if not text_body.strip():
        return []

    import re
    paragraphs = [p.strip() for p in re.split(r'\n\s*\n', text_body) if p.strip()]

    # Expand each paragraph into sub-chunks no larger than chunk_size.
    segments: list[str] = []
    for para in paragraphs:
        if len(para) <= chunk_size:
            segments.append(para)
        else:
            sentences = _split_sentences(para)
            for sent in sentences:
                if len(sent) <= chunk_size:
                    segments.append(sent)
                else:
                    segments.extend(_split_words(sent, chunk_size))

    if not segments:
        return []

    # Merge segments into chunks of at most chunk_size, carrying overlap forward.
    chunks: list[str] = []
    current = segments[0]
    for seg in segments[1:]:
        candidate = current + " " + seg
        if len(candidate) <= chunk_size:
            current = candidate
        else:
            if len(current) >= _MIN_CHUNK_LENGTH:
                chunks.append(current)
            # Carry the tail of the previous chunk as overlap.
            overlap_text = current[-overlap:] if len(current) > overlap else current
            current = (overlap_text + " " + seg).strip()
    if len(current) >= _MIN_CHUNK_LENGTH:
        chunks.append(current)
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

    # The pre-flight SELECT and embedding call happen outside the transaction so
    # we don't hold an open transaction during the (potentially slow) OpenAI call.
    # The write phase is wrapped in a single atomic begin() block below.
    async with db.begin():
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
                "content": content if isinstance(content, str) else json.dumps(content),
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
                # Batch insert all chunks in one statement instead of N round-trips.
                rows = ",".join(
                    f"(:pid::uuid, {i}, :text_{i}, :emb_{i}::vector)"
                    for i in range(len(chunks))
                )
                batch_params: dict[str, Any] = {"pid": page_id}
                for i, (chunk, emb) in enumerate(zip(chunks, embeddings)):
                    batch_params[f"text_{i}"] = chunk
                    batch_params[f"emb_{i}"] = "[" + ",".join(map(str, emb)) + "]"
                await db.execute(
                    text(f"INSERT INTO wiki_chunks (page_id, chunk_idx, text, embedding) VALUES {rows}"),
                    batch_params,
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
                    "citation_id": c.citation_id,
                    "source_type": c.source_type,
                    "source_id": c.source_id,
                    "label": c.label,
                },
            )

        # Dispatch wiki_updated notifications to all subscribers inside the same
        # transaction so notification rows are atomic with the page write.
        if existing_row:
            subs = await db.execute(
                text("""
                    SELECT user_id FROM wiki_subscriptions
                    WHERE page_id = :pid::uuid AND user_id != :author
                """),
                {"pid": page_id, "author": created_by},
            )
            for sub in subs:
                await db.execute(
                    text("""
                        INSERT INTO notifications (user_id, type, payload)
                        VALUES (:uid, 'wiki_updated', :payload::jsonb)
                    """),
                    {
                        "uid": sub.user_id,
                        "payload": json.dumps({"page_id": page_id, "slug": slug, "title": title}),
                    },
                )

    return page_id


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
        return dict(earliest_row._mapping) if earliest_row else page
    return {**page, **dict(rev_row._mapping)}


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
        # No-op patch: check existence without a misleading 404.
        exists = await db.execute(
            text("SELECT 1 FROM wiki_pages WHERE slug = :slug"),
            {"slug": slug},
        )
        return {"found": exists.one_or_none() is not None, "updated": False}

    sets.append("updated_by = :updated_by")
    params["updated_by"] = updated_by
    async with db.begin():
        result = await db.execute(
            text(f"UPDATE wiki_pages SET {', '.join(sets)} WHERE slug = :slug RETURNING id"),
            params,
        )
        found = result.one_or_none() is not None
    return {"found": found, "updated": True}
