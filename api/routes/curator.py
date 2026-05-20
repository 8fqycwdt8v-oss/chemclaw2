"""Curator inbox — single endpoint aggregating things waiting for human attention.

V2 created three independent sources of "review me" items:
  - Wiki pages with `needs_review=true` (created by document uploads,
    agent-authored drafts, manual marking)
  - Campaign steps in `pending_approval` (agent flagged as high-risk)
  - Wiki contradictions in `resolved=false` state

Users had to know about three endpoints to triage them. This router
aggregates all three into `GET /api/curator/inbox` with a single
`total_pending` count for badge UIs and three labelled buckets.

Buckets are owner-scoped where applicable; wiki contradictions are
collaborative so they surface to every authenticated caller.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.db.connection import get_db
from api.db.queries.campaigns import list_steps_awaiting_approval
from api.db.queries.contradictions import list_contradictions
from api.db.queries.rate_limit import rate_limit
from api.db.queries.wiki_read import list_wiki_needs_review

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/api/curator/inbox",
    dependencies=[Depends(rate_limit("curator-inbox", 30))],
)
async def get_curator_inbox(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
) -> dict[str, Any]:
    """Aggregate the caller's curator queue into a single response."""
    wiki_pages = await list_wiki_needs_review(db, user_id, limit=50)
    step_approvals = await list_steps_awaiting_approval(db, user_id, limit=50)
    # Contradictions are collaborative — every caller sees the same list.
    contradictions = await list_contradictions(db, resolved=False, limit=50)
    return {
        "wiki_needs_review": wiki_pages,
        "step_approvals": step_approvals,
        "contradictions": contradictions,
        "total_pending": (
            len(wiki_pages) + len(step_approvals) + len(contradictions)
        ),
    }
