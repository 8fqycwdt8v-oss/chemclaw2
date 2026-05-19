"""Notification routes — GET/PATCH /api/notifications, GET /api/notifications/stream."""
from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.db.connection import get_db
from api.db.queries.notifications import (
    count_unread,
    list_notifications,
    mark_all_read,
    mark_read,
)
from api.db.queries.rate_limit import rate_limit

router = APIRouter()

logger = logging.getLogger(__name__)


class NotificationMarkBody(BaseModel):
    ids: list[str] | None = None
    all: bool = False


@router.get("/api/notifications", dependencies=[Depends(rate_limit("notifications-read", 60))])
async def get_notifications(
    unread_only: bool = Query(True),
    limit: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
) -> dict[str, Any]:
    notifications = await list_notifications(db, user_id, unread_only=unread_only, limit=limit)
    unread_count = await count_unread(db, user_id)
    return {"notifications": notifications, "unread_count": unread_count}


@router.patch("/api/notifications", dependencies=[Depends(rate_limit("notifications-write", 30))])
async def patch_notifications(
    body: NotificationMarkBody,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
) -> dict[str, Any]:
    if not body.ids and not body.all:
        return {"marked_read": 0}

    if body.ids:
        updated = await mark_read(db, user_id, body.ids)
    else:
        updated = await mark_all_read(db, user_id)

    return {"marked_read": updated}


@router.get("/api/notifications/stream", dependencies=[Depends(rate_limit("notifications-stream", 10))])
async def notification_stream(
    user_id: str = Depends(get_current_user),
) -> StreamingResponse:
    import asyncio

    from api.db.connection import async_session_factory

    async def gen():
        try:
            while True:
                if async_session_factory is not None:
                    try:
                        async with async_session_factory() as poll_db:
                            items = await list_notifications(poll_db, user_id, unread_only=True, limit=20)
                        serialized = [
                            {
                                **n,
                                "created_at": (
                                    n["created_at"].isoformat()
                                    if hasattr(n["created_at"], "isoformat")
                                    else str(n["created_at"])
                                ),
                            }
                            for n in items
                        ]
                        if serialized:
                            yield f"data: {json.dumps({'type': 'notifications', 'items': serialized})}\n\n"
                    except Exception:
                        logger.exception("notification_stream_error user=%s", user_id)
                yield ": keepalive\n\n"
                await asyncio.sleep(30)
        except (asyncio.CancelledError, GeneratorExit):
            logger.info("notification_stream_closed user=%s", user_id)

    return StreamingResponse(
        gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-store",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
