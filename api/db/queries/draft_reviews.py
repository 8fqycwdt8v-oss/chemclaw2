"""Persist + list automated draft reviews (the ensemble-reviewer output).

`create_draft_review` stores one meta-review (decision + consensus score
+ the individual reviewer scores). `list_draft_reviews_needing_attention`
is the curator-inbox view: a caller's reviews whose decision is not
'accept'. Owner-scoped on `created_by` per CLAUDE.md.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.queries._helpers import clamp_limit, rows_to_dicts, validate_enum

logger = logging.getLogger(__name__)

_VALID_KINDS = {"report", "wiki"}
_VALID_DECISIONS = {"accept", "revise", "reject"}


async def create_draft_review(
    db: AsyncSession,
    *,
    kind: str,
    decision: str,
    overall: int,
    summary: str,
    top_issues: list[str],
    reviewer_scores: list[dict[str, Any]],
    created_by: str,
    page_slug: str | None = None,
    investigation_id: str | None = None,
) -> str:
    """Insert one meta-review row. Returns the new id."""
    validate_enum(kind, _VALID_KINDS, "kind")
    validate_enum(decision, _VALID_DECISIONS, "decision")
    async with db.begin():
        result = await db.execute(
            text("""
                INSERT INTO draft_reviews
                    (kind, page_slug, investigation_id, decision, overall,
                     summary, top_issues, reviewer_scores, created_by)
                VALUES (:kind, :slug, CAST(:iid AS uuid), :decision, :overall,
                        :summary, CAST(:issues AS jsonb), CAST(:scores AS jsonb), :uid)
                RETURNING id::text
            """),
            {
                "kind": kind,
                "slug": page_slug,
                "iid": investigation_id,
                "decision": decision,
                "overall": overall,
                "summary": summary,
                "issues": json.dumps(top_issues),
                "scores": json.dumps(reviewer_scores),
                "uid": created_by,
            },
        )
        return result.scalar_one()


async def list_draft_reviews_needing_attention(
    db: AsyncSession,
    user_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """The caller's non-accepted draft reviews, newest first."""
    safe_limit = clamp_limit(limit, 200)
    result = await db.execute(
        text("""
            SELECT id::text, kind, page_slug, investigation_id::text,
                   decision, overall, summary, top_issues, created_at
            FROM draft_reviews
            WHERE created_by = :uid
              AND decision <> 'accept'
            ORDER BY created_at DESC, id DESC
            LIMIT :lim
        """),
        {"uid": user_id, "lim": safe_limit},
    )
    return rows_to_dicts(result)
