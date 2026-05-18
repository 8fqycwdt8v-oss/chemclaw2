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
    cancel_campaign,
    get_campaign_with_steps,
    list_user_campaigns,
)
from api.db.queries.rate_limit import pg_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter()

_UUID_RE = re.compile(r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I)


class CampaignPatchBody(BaseModel):
    status: Literal["failed"]


@router.get("/api/campaigns")
async def list_campaigns(
    cursor: str | None = Query(None),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    limited = await pg_rate_limit(db, f"campaigns-list:{user_id}", 30, 60_000)
    if limited["limited"]:
        logger.info("rate_limit_denied: campaigns-list user=%s", user_id)
        raise HTTPException(status_code=429, detail="Too many requests")

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


@router.get("/api/campaigns/{campaign_id}")
async def get_campaign(
    campaign_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    limited = await pg_rate_limit(db, f"campaigns-get:{user_id}", 60, 60_000)
    if limited["limited"]:
        logger.info("rate_limit_denied: campaigns-get user=%s", user_id)
        raise HTTPException(status_code=429, detail="Too many requests")

    result = await get_campaign_with_steps(db, campaign_id, user_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Campaign not found")

    return result


@router.patch("/api/campaigns/{campaign_id}")
async def patch_campaign(
    campaign_id: str,
    body: CampaignPatchBody,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    limited = await pg_rate_limit(db, f"campaigns-patch:{user_id}", 20, 60_000)
    if limited["limited"]:
        logger.info("rate_limit_denied: campaigns-patch user=%s", user_id)
        raise HTTPException(status_code=429, detail="Too many requests")

    cancelled = await cancel_campaign(db, campaign_id, user_id)
    if not cancelled:
        raise HTTPException(status_code=404, detail="Campaign not found or already in a terminal state")

    return {"ok": True}
