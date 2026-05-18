"""Chat route — POST /api/chat (SSE streaming agent)."""
from __future__ import annotations

import logging
import uuid
from typing import Any

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.db.connection import get_db, async_session_factory
from api.db.queries.rate_limit import pg_rate_limit

router = APIRouter()
logger = logging.getLogger(__name__)

MAX_PROMPT_BYTES = 100_000
MAX_JUSTIFICATION_LEN = 2000


class ChatRequest(BaseModel):
    prompt: str
    session_id: str | None = None
    override_justification: str | None = None
    plan_mode: bool | None = None

    @field_validator("prompt")
    @classmethod
    def prompt_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("prompt must not be empty")
        if len(v.encode()) > MAX_PROMPT_BYTES:
            raise ValueError("prompt too large")
        return v

    @field_validator("session_id")
    @classmethod
    def valid_uuid(cls, v: str | None) -> str | None:
        if v is None:
            return v
        try:
            uuid.UUID(v)
        except ValueError:
            return None
        return v

    @field_validator("override_justification")
    @classmethod
    def justification_bounds(cls, v: str | None) -> str | None:
        if v is None:
            return v
        v = v.strip()
        if v and (len(v) < 20 or len(v) > MAX_JUSTIFICATION_LEN):
            return None
        return v


def _error_stream(msg: str, status: int = 400):
    import json

    async def gen():
        yield f"data: {json.dumps({'type': 'error', 'message': msg})}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(
        gen(),
        status_code=status,
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"},
    )


@router.post("/api/chat")
async def chat(
    body: ChatRequest,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    from api.agent.hooks import scheduled_substance_gate
    from api.agent.runner import run_agent_streaming
    from api.db.queries.budgets import record_override

    limited = await pg_rate_limit(db, f"chat:{user_id}", 20, 60_000)
    if limited["limited"]:
        logger.warning("chat_rate_limited user=%s", user_id)
        return _error_stream("Too many requests — please wait before sending another message", 429)

    session_id = body.session_id or str(uuid.uuid4())

    gate = scheduled_substance_gate(body.prompt)
    if gate["blocked"]:
        justification = body.override_justification
        if not justification:
            import json
            async def blocked_gen():
                yield f"data: {json.dumps({'type': 'error', 'message': gate['reason'], 'blocked': True, 'override_available': True})}\n\n"
                yield "data: [DONE]\n\n"
            return StreamingResponse(
                blocked_gen(), status_code=403, media_type="text/event-stream",
                headers={"Cache-Control": "no-cache, no-store", "X-Accel-Buffering": "no"},
            )
        await record_override(db, session_id, user_id, "scheduled_substance", justification, body.prompt)

    if async_session_factory is None:
        return _error_stream("Database not initialised", 503)

    return StreamingResponse(
        run_agent_streaming(
            body.prompt, user_id, session_id, async_session_factory,
            plan_mode=body.plan_mode is True,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-store", "Connection": "keep-alive", "X-Accel-Buffering": "no"},
    )
