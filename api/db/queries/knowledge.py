"""Knowledge-base queries — papers, external facts, and compound properties.

Upsert/insert functions manage their own transactions via `async with db.begin()`.
Read-only functions do NOT commit.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def search_papers(
    db: AsyncSession,
    query: str,
    limit: int = 5,
) -> list[dict[str, Any]]:
    """Full-text search across title, abstract, and content_text.

    Returns up to limit rows ordered by relevance (ts_rank DESC).
    """
    result = await db.execute(
        text("""
            SELECT id::text, doi, url, title, abstract, created_at
            FROM papers
            WHERE to_tsvector('english',
                      coalesce(title, '')
                      || ' ' || coalesce(abstract, '')
                      || ' ' || coalesce(content_text, '')
                  ) @@ plainto_tsquery('english', :q)
            ORDER BY ts_rank(
                to_tsvector('english',
                    coalesce(title, '')
                    || ' ' || coalesce(abstract, '')
                    || ' ' || coalesce(content_text, '')
                ),
                plainto_tsquery('english', :q)
            ) DESC
            LIMIT :lim
        """),
        {"q": query, "lim": limit},
    )
    return [dict(r._mapping) for r in result]


async def search_external_facts(
    db: AsyncSession,
    query: str | None = None,
    source_type: str | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Search external facts with optional FTS on content_text and/or source_type filter."""
    params: dict[str, Any] = {"lim": limit}
    fts_clause = ""
    if query is not None:
        fts_clause = "AND to_tsvector('english', coalesce(content_text, '')) @@ plainto_tsquery('english', :q)"
        params["q"] = query
    source_clause = ""
    if source_type is not None:
        source_clause = "AND source_type = :source_type"
        params["source_type"] = source_type
    result = await db.execute(
        text(f"""
            SELECT id::text, source_type, source_id, content_text, last_seen
            FROM external_facts
            WHERE TRUE
              {fts_clause}
              {source_clause}
            ORDER BY last_seen DESC
            LIMIT :lim
        """),
        params,
    )
    return [dict(r._mapping) for r in result]


async def lookup_compound_properties(
    db: AsyncSession,
    compound_id: str,
) -> list[dict[str, Any]]:
    """Return all properties for a compound, newest first."""
    result = await db.execute(
        text("""
            SELECT id::text, name, value_num, value_text, unit, method,
                   source_citation_id::text, measured_at
            FROM properties
            WHERE compound_id = CAST(:cid AS uuid)
            ORDER BY created_at DESC
        """),
        {"cid": compound_id},
    )
    return [dict(r._mapping) for r in result]


async def upsert_paper(
    db: AsyncSession,
    url: str,
    title: str,
    doi: str | None = None,
    pubmed_id: str | None = None,
    abstract: str | None = None,
    content_text: str | None = None,
    created_by: str | None = None,
) -> tuple[str, bool]:
    """Insert or update a paper record.

    When doi is provided, conflicts on doi update title and abstract (the paper
    is the same document, metadata may have been refined).

    When doi is None a plain INSERT is issued; if a concurrent insert already
    created the same row the function returns the existing id with was_inserted=False.

    Returns (id, was_inserted).
    """
    async with db.begin():
        if doi is not None:
            result = await db.execute(
                text("""
                    INSERT INTO papers (url, title, doi, pubmed_id, abstract,
                                        content_text, created_by)
                    VALUES (:url, :title, :doi, :pubmed_id, :abstract,
                            :content_text, :created_by)
                    ON CONFLICT (doi) DO UPDATE
                        SET title        = EXCLUDED.title,
                            abstract     = EXCLUDED.abstract,
                            content_text = EXCLUDED.content_text
                    RETURNING id::text,
                              (xmax = 0) AS inserted
                """),
                {
                    "url": url,
                    "title": title,
                    "doi": doi,
                    "pubmed_id": pubmed_id,
                    "abstract": abstract,
                    "content_text": content_text,
                    "created_by": created_by,
                },
            )
            row = result.one()
            return row[0], bool(row[1])
        else:
            result = await db.execute(
                text("""
                    INSERT INTO papers (url, title, doi, pubmed_id, abstract,
                                        content_text, created_by)
                    VALUES (:url, :title, NULL, :pubmed_id, :abstract,
                            :content_text, :created_by)
                    RETURNING id::text
                """),
                {
                    "url": url,
                    "title": title,
                    "pubmed_id": pubmed_id,
                    "abstract": abstract,
                    "content_text": content_text,
                    "created_by": created_by,
                },
            )
            row_id = result.scalar_one()
            return row_id, True


async def upsert_external_fact(
    db: AsyncSession,
    source_type: str,
    source_id: str,
    payload: dict[str, Any],
    content_text: str,
    fetched_by: str,
) -> tuple[str, bool]:
    """Insert or update an external fact keyed by (source_type, source_id).

    On conflict refreshes payload, content_text, last_seen, and fetched_by.
    Returns (id, was_inserted).
    """
    async with db.begin():
        result = await db.execute(
            text("""
                INSERT INTO external_facts
                    (source_type, source_id, payload, content_text,
                     fetched_by, first_seen, last_seen)
                VALUES (:source_type, :source_id, CAST(:payload AS jsonb), :content_text,
                        :fetched_by, now(), now())
                ON CONFLICT (source_type, source_id) DO UPDATE
                    SET payload      = EXCLUDED.payload,
                        content_text = EXCLUDED.content_text,
                        last_seen    = now(),
                        fetched_by   = EXCLUDED.fetched_by
                RETURNING id::text,
                          (xmax = 0) AS inserted
            """),
            {
                "source_type": source_type,
                "source_id": source_id,
                "payload": json.dumps(payload),
                "content_text": content_text,
                "fetched_by": fetched_by,
            },
        )
        row = result.one()
        return row[0], bool(row[1])


async def get_external_fact_by_source_id(
    db: AsyncSession,
    source_id: str,
) -> dict[str, Any] | None:
    """Return the external_fact row matching source_id exactly, or None."""
    result = await db.execute(
        text("""
            SELECT id::text, source_type, source_id, payload, content_text, last_seen
            FROM external_facts
            WHERE source_id = :sid
            LIMIT 1
        """),
        {"sid": source_id},
    )
    row = result.one_or_none()
    return dict(row._mapping) if row else None


async def insert_compound_property(
    db: AsyncSession,
    compound_id: str,
    name: str,
    created_by: str,
    value_num: float | None = None,
    value_text: str | None = None,
    unit: str | None = None,
    method: str | None = None,
    source_citation_id: str | None = None,
) -> str:
    """Insert a measured property for a compound. Returns the new row's id.

    Raises ValueError when neither value_num nor value_text is provided.
    """
    if value_num is None and value_text is None:
        raise ValueError("At least one of value_num or value_text must be provided.")
    async with db.begin():
        result = await db.execute(
            text("""
                INSERT INTO properties
                    (compound_id, name, value_num, value_text, unit, method,
                     source_citation_id, created_by)
                VALUES (CAST(:cid AS uuid), :name, :value_num, :value_text, :unit, :method,
                        CAST(:source_citation_id AS uuid), :created_by)
                RETURNING id::text
            """),
            {
                "cid": compound_id,
                "name": name,
                "value_num": value_num,
                "value_text": value_text,
                "unit": unit,
                "method": method,
                "source_citation_id": source_citation_id,
                "created_by": created_by,
            },
        )
        return result.scalar_one()
