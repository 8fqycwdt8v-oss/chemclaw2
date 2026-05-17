"""Campaign queries — Python port of packages/db/src/queries/campaigns.ts.

Callers are responsible for transaction management. These functions do NOT
call db.commit() — wrap multi-step operations in `async with db.begin():`.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

TERMINAL_STATUSES = ('complete', 'failed')
NON_TERMINAL_STATUSES = ('planning', 'awaiting_input', 'running')


async def create_campaign(
    db: AsyncSession,
    session_id: str,
    created_by: str,
    target_smiles: str | None = None,
) -> str:
    result = await db.execute(
        text("""
            INSERT INTO synthesis_campaigns (session_id, created_by, target_smiles)
            VALUES (:session_id, :created_by, :target_smiles)
            RETURNING id::text
        """),
        {"session_id": session_id, "created_by": created_by, "target_smiles": target_smiles},
    )
    return result.scalar_one()


async def update_campaign_status(
    db: AsyncSession,
    campaign_id: str,
    user_id: str,
    status: str,
    plan: dict[str, Any] | None = None,
) -> None:
    """Update campaign status. Only the campaign owner can update.

    Does NOT commit — caller manages the transaction.
    Source-state predicate excludes terminal statuses to prevent
    double-transitions.
    """
    plan_clause = ", plan = :plan::jsonb" if plan is not None else ""
    params: dict[str, Any] = {
        "id": campaign_id,
        "user_id": user_id,
        "status": status,
        "statuses": list(NON_TERMINAL_STATUSES),
    }
    if plan is not None:
        params["plan"] = json.dumps(plan)
    await db.execute(
        text(f"""
            UPDATE synthesis_campaigns
            SET status = :status, updated_at = now(){plan_clause}
            WHERE id = :id::uuid
              AND created_by = :user_id
              AND status = ANY(:statuses)
        """),
        params,
    )


async def get_campaign_by_session(
    db: AsyncSession,
    session_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    result = await db.execute(
        text("""
            SELECT id::text, session_id, target_smiles, status, plan,
                   created_by, created_at, updated_at
            FROM synthesis_campaigns
            WHERE session_id = :session_id
              AND created_by = :user_id
              AND status != ALL(:terminal)
            ORDER BY created_at DESC
            LIMIT 1
        """),
        {"session_id": session_id, "user_id": user_id, "terminal": list(TERMINAL_STATUSES)},
    )
    row = result.one_or_none()
    return dict(row._mapping) if row else None


async def add_campaign_step(
    db: AsyncSession,
    campaign_id: str,
    step_idx: int,
    reaction_smiles: str | None = None,
    conditions: str | None = None,
) -> str:
    """Insert a campaign step. Does NOT commit — caller manages the transaction."""
    result = await db.execute(
        text("""
            INSERT INTO campaign_steps (campaign_id, step_idx, reaction_smiles, conditions)
            VALUES (:campaign_id::uuid, :step_idx, :reaction_smiles, :conditions)
            ON CONFLICT (campaign_id, step_idx) DO UPDATE
                SET reaction_smiles = EXCLUDED.reaction_smiles,
                    conditions      = EXCLUDED.conditions
            RETURNING id::text
        """),
        {
            "campaign_id": campaign_id,
            "step_idx": step_idx,
            "reaction_smiles": reaction_smiles,
            "conditions": conditions,
        },
    )
    return result.scalar_one()


async def get_pending_campaign_steps(
    db: AsyncSession,
    campaign_id: str,
) -> list[dict[str, Any]]:
    result = await db.execute(
        text("""
            SELECT id::text, step_idx, reaction_smiles, conditions, status,
                   retry_count, next_retry_at
            FROM campaign_steps
            WHERE campaign_id = :campaign_id::uuid AND status = 'pending'
            ORDER BY step_idx
        """),
        {"campaign_id": campaign_id},
    )
    return [dict(r._mapping) for r in result]
