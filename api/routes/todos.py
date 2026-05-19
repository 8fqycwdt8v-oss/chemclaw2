"""Todos routes — GET/PUT /api/todos/{session_id}, PATCH /api/todos/{session_id}/{todo_id}."""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.db.connection import get_db
from api.db.queries.rate_limit import make_key, pg_rate_limit
from api.db.queries.todos import list_todos, mark_todo_done, upsert_todos

logger = logging.getLogger(__name__)

router = APIRouter()


class TodoItem(BaseModel):
    text: str = Field(..., min_length=1)
    status: Literal["pending", "done"]
    position: int = Field(..., ge=0)


class TodosPutBody(BaseModel):
    todos: list[TodoItem]


class TodoPatchBody(BaseModel):
    status: Literal["done"]


@router.get("/api/todos/{session_id}")
async def get_todos(
    session_id: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    limited = await pg_rate_limit(db, make_key("todos-read", user_id), 60, 60_000)
    if limited["limited"]:
        raise HTTPException(status_code=429, detail="Too many requests")
    todos = await list_todos(db, session_id, user_id)
    return {"todos": todos}


@router.put("/api/todos/{session_id}")
async def replace_todos(
    session_id: str,
    body: TodosPutBody,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    limited = await pg_rate_limit(db, make_key("todos-put", user_id), 30, 60_000)
    if limited["limited"]:
        raise HTTPException(status_code=429, detail="Too many requests")
    await upsert_todos(db, session_id, user_id, [t.model_dump() for t in body.todos])
    return {"ok": True}


@router.patch("/api/todos/{session_id}/{todo_id}")
async def patch_todo(
    session_id: str,
    todo_id: str,
    body: TodoPatchBody,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    limited = await pg_rate_limit(db, make_key("todos-patch", user_id), 60, 60_000)
    if limited["limited"]:
        raise HTTPException(status_code=429, detail="Too many requests")
    updated = await mark_todo_done(db, todo_id, user_id)
    if not updated:
        raise HTTPException(status_code=404, detail="Todo not found")
    return {"ok": True}
