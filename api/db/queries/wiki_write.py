"""Write-side wiki queries: upsert + patch + chunking.

Split from the original api/db/queries/wiki.py (was 521 LOC) — see C1 in
BACKLOG.md. Pair with wiki_read.py (list / get / search / revisions).
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_MIN_CHUNK_LENGTH = 50


def _split_sentences(text_body: str) -> list[str]:
    """Split on sentence-ending punctuation followed by whitespace."""
    parts = re.split(r"(?<=[.!?])\s+", text_body)
    return [p.strip() for p in parts if p.strip()]


def _split_words(text_body: str, max_size: int) -> list[str]:
    """Greedy word-boundary split that never exceeds max_size."""
    words = text_body.split()
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

    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text_body) if p.strip()]

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

    chunks: list[str] = []
    current = segments[0]
    for seg in segments[1:]:
        candidate = current + " " + seg
        if len(candidate) <= chunk_size:
            current = candidate
        else:
            if len(current) >= _MIN_CHUNK_LENGTH:
                chunks.append(current)
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
    # Pre-flight: skip re-embedding if content unchanged.
    # SQLAlchemy 2.0 async auto-begins a transaction on the SELECT below; we
    # close it before the slow embed call so the explicit `async with
    # db.begin():` write block further down doesn't collide.
    # IMPORTANT: only roll back the tx if WE autobegan it. If the caller
    # had a transaction active before calling this function, rollback would
    # silently discard the caller's uncommitted work.
    pre_existing_tx = db.in_transaction()
    existing = await db.execute(
        text("SELECT content_text FROM wiki_pages WHERE slug = :slug"),
        {"slug": slug},
    )
    existing_row = existing.one_or_none()
    if not pre_existing_tx:
        await db.rollback()
    content_changed = not existing_row or existing_row.content_text != content_text

    chunks: list[str] = []
    embeddings: list[list[float]] = []
    if content_changed:
        chunks = chunk_text(content_text)
        if chunks:
            try:
                embeddings = await embed_fn(chunks)
            except Exception:
                logger.exception("wiki_embed_failed slug=%s", slug)
                raise
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
                VALUES (:slug, :title, CAST(:content AS jsonb), :content_text, :created_by, :updated_by{meta_vals})
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
                text("DELETE FROM wiki_chunks WHERE page_id = CAST(:pid AS uuid)"),
                {"pid": page_id},
            )
            if chunks:
                # Batch insert all chunks in one statement instead of N round-trips.
                rows = ",".join(
                    f"(CAST(:pid AS uuid), {i}, :text_{i}, CAST(:emb_{i} AS vector))"
                    for i in range(len(chunks))
                )
                batch_params: dict[str, Any] = {"pid": page_id}
                for i, (chunk, emb) in enumerate(zip(chunks, embeddings, strict=True)):
                    batch_params[f"text_{i}"] = chunk
                    batch_params[f"emb_{i}"] = "[" + ",".join(map(str, emb)) + "]"
                await db.execute(
                    text(f"INSERT INTO wiki_chunks (page_id, chunk_idx, text, embedding) VALUES {rows}"),
                    batch_params,
                )

        # Always replace citations
        await db.execute(
            text("DELETE FROM wiki_citations WHERE page_id = CAST(:pid AS uuid)"),
            {"pid": page_id},
        )
        for c in citations:
            # Support both Pydantic model instances and plain dicts.
            if isinstance(c, dict):
                cit_id = c["citation_id"]
                src_type = c["source_type"]
                src_id = c.get("source_id")
                label = c.get("label")
            else:
                cit_id = c.citation_id
                src_type = c.source_type
                src_id = c.source_id
                label = c.label
            await db.execute(
                text("""
                    INSERT INTO wiki_citations (page_id, citation_id, source_type, source_id, label)
                    VALUES (CAST(:page_id AS uuid), :citation_id, :source_type, :source_id, :label)
                """),
                {
                    "page_id": page_id,
                    "citation_id": cit_id,
                    "source_type": src_type,
                    "source_id": src_id,
                    "label": label,
                },
            )

        # Dispatch wiki_updated notifications to all subscribers inside the same
        # transaction so notification rows are atomic with the page write.
        if existing_row:
            subs = await db.execute(
                text("""
                    SELECT user_id FROM wiki_subscriptions
                    WHERE page_id = CAST(:pid AS uuid) AND user_id != :author
                """),
                {"pid": page_id, "author": created_by},
            )
            for sub in subs:
                await db.execute(
                    text("""
                        INSERT INTO notifications (user_id, type, payload)
                        VALUES (:uid, 'wiki_updated', CAST(:payload AS jsonb))
                    """),
                    {
                        "uid": sub.user_id,
                        "payload": json.dumps({"page_id": page_id, "slug": slug, "title": title}),
                    },
                )

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
        # No-op patch: check existence without a misleading 404.
        exists = await db.execute(
            text("SELECT 1 FROM wiki_pages WHERE slug = :slug"),
            {"slug": slug},
        )
        return {"found": exists.one_or_none() is not None, "updated": False}

    sets.append("updated_by = :updated_by")
    params["updated_by"] = updated_by
    async with db.begin():
        # Owner-scope predicate: route handlers also enforce this with a 403,
        # but per CLAUDE.md every per-user UPDATE includes the creator in WHERE
        # so a future caller cannot accidentally bypass.
        result = await db.execute(
            text(
                f"UPDATE wiki_pages SET {', '.join(sets)} "
                f"WHERE slug = :slug AND created_by = :updated_by RETURNING id"
            ),
            params,
        )
        found = result.one_or_none() is not None
    return {"found": found, "updated": True}
