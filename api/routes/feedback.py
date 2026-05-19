"""Feedback routes — POST /api/feedback, GET /api/feedback/{session_id}."""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.db.connection import get_db
from api.db.queries.feedback import list_session_feedback, record_feedback
from api.db.queries.rate_limit import rate_limit

logger = logging.getLogger(__name__)
router = APIRouter()


class FeedbackBody(BaseModel):
    session_id: str = Field(..., min_length=1)
    turn_index: int = Field(..., ge=0)
    score: Literal[1, -1]
    reason: str | None = None


@router.post("/api/feedback", dependencies=[Depends(rate_limit("feedback", 60))])
async def post_feedback(
    body: FeedbackBody,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    feedback_id = await record_feedback(
        db,
        session_id=body.session_id,
        turn_index=body.turn_index,
        score=body.score,
        user_id=user_id,
        reason=body.reason,
    )
    return {"id": feedback_id}


@router.get("/api/feedback/{session_id}", dependencies=[Depends(rate_limit("feedback-read", 60))])
async def get_feedback(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    feedback = await list_session_feedback(db, session_id=session_id, user_id=user_id)
    return {"feedback": feedback}
