"""Wiki contradiction queries — tracks conflicting citations on wiki pages.

Functions that mutate state manage their own transaction via `async with db.begin()`.
Read-only functions do NOT commit — no transaction management required.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.queries._helpers import rows_to_dicts, validate_enum

logger = logging.getLogger(__name__)

_VALID_PROPOSED_WINNERS = frozenset(("a", "b", "inconclusive"))


async def list_contradictions(
    db: AsyncSession,
    page_id: str | None = None,
    resolved: bool = False,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return contradictions, optionally filtered by page and resolution status.

    When resolved=False (default) only unresolved rows are returned.
    When resolved=True all rows are returned regardless of resolution.
    """
    params: dict[str, Any] = {"lim": limit}
    page_clause = ""
    if page_id is not None:
        page_clause = "AND page_id = CAST(:pid AS uuid)"
        params["pid"] = page_id
    resolved_clause = "AND resolved_by IS NULL" if not resolved else ""
    result = await db.execute(
        text(f"""
            SELECT id::text, page_id::text, citation_a, citation_b,
                   proposed_winner, reason, resolved_by, created_at
            FROM wiki_contradictions
            WHERE TRUE
              {page_clause}
              {resolved_clause}
            ORDER BY created_at DESC
            LIMIT :lim
        """),
        params,
    )
    return rows_to_dicts(result)


async def create_contradiction(
    db: AsyncSession,
    page_id: str,
    citation_a: str,
    citation_b: str,
    proposed_winner: str,
    reason: str,
) -> str:
    """Insert a new contradiction record. Returns the new row's id as a string."""
    validate_enum(proposed_winner, _VALID_PROPOSED_WINNERS, "proposed_winner")
    async with db.begin():
        result = await db.execute(
            text("""
                INSERT INTO wiki_contradictions
                    (page_id, citation_a, citation_b, proposed_winner, reason)
                VALUES (CAST(:pid AS uuid), :citation_a, :citation_b, :proposed_winner, :reason)
                RETURNING id::text
            """),
            {
                "pid": page_id,
                "citation_a": citation_a,
                "citation_b": citation_b,
                "proposed_winner": proposed_winner,
                "reason": reason,
            },
        )
        return result.scalar_one()


async def resolve_contradiction(
    db: AsyncSession,
    contradiction_id: str,
    resolved_by: str,
    page_id: str | None = None,
) -> bool:
    """Mark a contradiction as resolved by resolved_by.

    Idempotent-safe: only updates when resolved_by IS NULL so the first resolver wins.
    page_id narrows the update to prevent cross-page ID guessing attacks.
    Returns True if the row was updated.
    """
    page_clause = "AND page_id = CAST(:pid AS uuid)" if page_id is not None else ""
    params: dict[str, Any] = {"cid": contradiction_id, "uid": resolved_by}
    if page_id is not None:
        params["pid"] = page_id
    async with db.begin():
        result = await db.execute(
            text(f"""
                UPDATE wiki_contradictions
                SET resolved_by = :uid
                WHERE id = CAST(:cid AS uuid) AND resolved_by IS NULL {page_clause}
                RETURNING id
            """),
            params,
        )
        return result.one_or_none() is not None
