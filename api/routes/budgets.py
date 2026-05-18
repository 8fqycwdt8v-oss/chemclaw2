"""Budget management routes — GET/PUT/DELETE /api/budgets/{project_key}."""
from __future__ import annotations

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.auth import get_current_user
from api.db.connection import get_db
from api.db.queries.budgets import (
    delete_project_budget,
    get_budget_with_spend,
    upsert_project_budget,
)
from api.db.queries.rate_limit import make_key, pg_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter()


def _check_ownership(project_key: str, user_id: str) -> None:
    expected = f"chemclaw2:{user_id}"
    if project_key != expected:
        logger.info("budget_ownership_denied: user=%s project_key=%s", user_id, project_key)
        raise HTTPException(status_code=403, detail="Access denied")


class BudgetUpsertBody(BaseModel):
    period: Literal["day", "week", "month"]
    tool_calls_cap: int | None = Field(default=None, ge=0)
    experiments_cap: int | None = Field(default=None, ge=0)
    tokens_cap: int | None = Field(default=None, ge=0)


@router.get("/api/budgets/{project_key}")
async def get_budget(
    project_key: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    _check_ownership(project_key, user_id)
    limited = await pg_rate_limit(db, make_key("budget-read", user_id), 60, 60_000)
    if limited["limited"]:
        raise HTTPException(status_code=429, detail="Too many requests")
    row = await get_budget_with_spend(db, project_key)
    if row is None:
        return {"budget": None, "spend": None}
    spend = row.pop("spend", None)
    return {"budget": row, "spend": spend}


@router.put("/api/budgets/{project_key}")
async def put_budget(
    project_key: str,
    body: BudgetUpsertBody,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    _check_ownership(project_key, user_id)
    limited = await pg_rate_limit(db, make_key("budget-write", user_id), 10, 60_000)
    if limited["limited"]:
        logger.warning("budget_put_rate_limited: user=%s", user_id)
        raise HTTPException(status_code=429, detail="Too many requests")
    await upsert_project_budget(
        db,
        project_key,
        body.period,
        body.tool_calls_cap,
        body.experiments_cap,
        body.tokens_cap,
        user_id,
    )
    return {"ok": True}


@router.delete("/api/budgets/{project_key}")
async def delete_budget(
    project_key: str,
    db: AsyncSession = Depends(get_db),
    user_id: str = Depends(get_current_user),
):
    _check_ownership(project_key, user_id)
    limited = await pg_rate_limit(db, make_key("budget-write", user_id), 10, 60_000)
    if limited["limited"]:
        logger.warning("budget_delete_rate_limited: user=%s", user_id)
        raise HTTPException(status_code=429, detail="Too many requests")
    deleted = await delete_project_budget(db, project_key)
    if not deleted:
        raise HTTPException(status_code=404, detail="Budget not found")
    return {"ok": True}
