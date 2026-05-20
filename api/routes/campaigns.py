"""Campaign routes — GET/PATCH /api/campaigns, GET /api/campaigns/{id}."""
from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.db.connection import get_db
from api.db.queries.campaigns import (
    approve_step,
    cancel_campaign,
    get_campaign_with_steps,
    list_steps_awaiting_approval,
    list_user_campaigns,
    reject_step,
)
from api.db.queries.rate_limit import rate_limit

logger = logging.getLogger(__name__)

router = APIRouter()

_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)


class CampaignPatchBody(BaseModel):
    status: Literal["failed"]


@router.get("/api/campaigns", dependencies=[Depends(rate_limit("campaigns-list", 30))])
async def list_campaigns(
    cursor: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    cursor_updated_at: datetime | None = None
    cursor_id: str | None = None
    if cursor:
        sep = cursor.rfind('_')
        if sep == -1:
            raise HTTPException(status_code=400, detail="Invalid cursor")
        try:
            cursor_updated_at = datetime.fromisoformat(cursor[:sep])
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid cursor")
        cursor_id = cursor[sep + 1:]
        if not _UUID_RE.match(cursor_id):
            raise HTTPException(status_code=400, detail="Invalid cursor")

    campaigns = await list_user_campaigns(db, user_id, 50, cursor_updated_at, cursor_id)

    next_cursor = None
    if len(campaigns) == 50:
        last = campaigns[-1]
        ts = last["updated_at"]
        if hasattr(ts, 'isoformat'):
            ts = ts.isoformat()
        else:
            ts = str(ts)
        next_cursor = f"{ts}_{last['id']}"

    return {"campaigns": campaigns, "nextCursor": next_cursor}


@router.get("/api/campaigns/{campaign_id}", dependencies=[Depends(rate_limit("campaigns-get", 60))])
async def get_campaign(
    campaign_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    result = await get_campaign_with_steps(db, campaign_id, user_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Campaign not found")

    return result


@router.patch("/api/campaigns/{campaign_id}", dependencies=[Depends(rate_limit("campaigns-patch", 20))])
async def patch_campaign(
    campaign_id: str,
    body: CampaignPatchBody,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    cancelled = await cancel_campaign(db, campaign_id, user_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="Campaign not found or already in a terminal state")

    return {"ok": True}


# ── Step approval ────────────────────────────────────────────────────────────

@router.get(
    "/api/campaigns/steps/awaiting-approval",
    dependencies=[Depends(rate_limit("campaigns-approval-list", 60))],
)
async def get_steps_awaiting_approval(
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """Return campaign steps in 'pending_approval' that the caller owns.

    Drives the "needs my approval" inbox. Steps stay here until the
    user calls POST .../approve or .../reject.
    """
    steps = await list_steps_awaiting_approval(db, user_id)
    return {"steps": steps}


@router.post(
    "/api/campaigns/{campaign_id}/steps/{step_idx}/approve",
    dependencies=[Depends(rate_limit("campaigns-step-approve", 30))],
)
async def approve_campaign_step(
    campaign_id: str,
    step_idx: int,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """Promote a step from 'pending_approval' to 'pending'. Owner-scoped."""
    if not _UUID_RE.match(campaign_id):
        raise HTTPException(status_code=400, detail="Invalid campaign id")
    if step_idx < 0:
        raise HTTPException(status_code=400, detail="step_idx must be non-negative")
    ok = await approve_step(db, campaign_id, step_idx, user_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail="Step not found, not awaiting approval, or not owned by you",
        )
    return {"ok": True}


@router.post(
    "/api/campaigns/{campaign_id}/steps/{step_idx}/reject",
    dependencies=[Depends(rate_limit("campaigns-step-reject", 30))],
)
async def reject_campaign_step(
    campaign_id: str,
    step_idx: int,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    """Mark a step as 'failed' (no retries). Owner-scoped."""
    if not _UUID_RE.match(campaign_id):
        raise HTTPException(status_code=400, detail="Invalid campaign id")
    if step_idx < 0:
        raise HTTPException(status_code=400, detail="step_idx must be non-negative")
    ok = await reject_step(db, campaign_id, step_idx, user_id)
    if not ok:
        raise HTTPException(
            status_code=404,
            detail="Step not found, not awaiting approval, or not owned by you",
        )
    return {"ok": True}
