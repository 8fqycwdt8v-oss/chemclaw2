"""Bi-temporal wiki queries: revision list, single-version fetch, as-of lookup.

Compliance §3.8 reproducibility needs these — the agent must be able to
recover any historical state of a wiki page (`get_wiki_page_at`) and
distinguish exact-vs-approximate temporal hits (the
`temporal_exact` / `temporal_warning` fields).

Split out of `wiki_read.py` to keep that module focused on current-state
read paths (search + list + get). All three functions read from
`wiki_revisions` (or join `wiki_pages` + `wiki_revisions` in the
as-of case).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


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
