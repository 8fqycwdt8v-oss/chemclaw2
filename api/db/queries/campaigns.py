"""Campaign queries — Python port of packages/db/src/queries/campaigns.ts.

Callers are responsible for transaction management. These functions do NOT
call db.commit() — wrap multi-step operations in `async with db.begin():`.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

TERMINAL_STATUSES = ('complete', 'failed')
NON_TERMINAL_STATUSES = ('planning', 'awaiting_input', 'running')
MAX_STEP_RETRIES = 3


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
    """Update campaign status for the campaign owner.

    Includes `created_by = :user_id` in the WHERE clause so a user can only
    advance their own campaigns. For system/worker calls use
    `system_advance_campaign()`.

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


async def system_advance_campaign(
    db: AsyncSession,
    campaign_id: str,
    status: str,
    plan: dict[str, Any] | None = None,
) -> None:
    """Advance a campaign status from a system/worker context (no ownership check).

    Only callable from background workers. Never expose via HTTP routes.
    Does NOT commit — caller manages the transaction.
    """
    plan_clause = ", plan = :plan::jsonb" if plan is not None else ""
    params: dict[str, Any] = {
        "id": campaign_id,
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


async def get_steps_for_retry(db: AsyncSession) -> list[dict[str, Any]]:
    """Return failed steps that are eligible for retry.

    Criteria (matching TypeScript campaign-worker.ts):
    - status = 'failed'
    - retry_count < MAX_STEP_RETRIES (3)
    - next_retry_at <= NOW() (backoff has elapsed)
    """
    result = await db.execute(
        text("""
            SELECT cs.id::text, cs.campaign_id::text, cs.step_idx,
                   cs.reaction_smiles, cs.conditions, cs.retry_count
            FROM campaign_steps cs
            WHERE cs.status = 'failed'
              AND cs.retry_count < :max_retries
              AND cs.next_retry_at <= now()
        """),
        {"max_retries": MAX_STEP_RETRIES},
    )
    return [dict(r._mapping) for r in result]


async def mark_step_failed(
    db: AsyncSession,
    step_id: str,
    retry_count: int,
) -> None:
    """Mark a step as failed and schedule its retry with exponential backoff.

    next_retry_at = now() + 2^retry_count minutes (matches TypeScript).
    Does NOT commit — caller manages the transaction.
    """
    backoff_minutes = 2 ** retry_count
    await db.execute(
        text("""
            UPDATE campaign_steps
            SET status        = 'failed',
                retry_count   = :retry_count,
                next_retry_at = now() + :backoff * interval '1 minute',
                updated_at    = now()
            WHERE id = :id::uuid
              AND status = 'pending'
        """),
        {"id": step_id, "retry_count": retry_count, "backoff": backoff_minutes},
    )


async def mark_step_complete(
    db: AsyncSession,
    step_id: str,
    result: dict[str, Any] | None = None,
) -> None:
    """Mark a step as complete, storing the optional result JSON.

    Does NOT commit — caller manages the transaction.
    """
    await db.execute(
        text("""
            UPDATE campaign_steps
            SET status     = 'complete',
                result     = :result::jsonb,
                updated_at = now()
            WHERE id = :id::uuid
              AND status = 'pending'
        """),
        {"id": step_id, "result": json.dumps(result) if result is not None else None},
    )


async def get_running_campaigns(db: AsyncSession) -> list[dict[str, Any]]:
    """Return all campaigns currently in 'running' status."""
    result = await db.execute(
        text("""
            SELECT id::text, session_id, created_by, target_smiles, plan
            FROM synthesis_campaigns
            WHERE status = 'running'
            ORDER BY updated_at
        """),
    )
    return [dict(r._mapping) for r in result]


async def all_steps_complete(db: AsyncSession, campaign_id: str) -> bool:
    """Return True if every step is complete AND the campaign has at least one step."""
    result = await db.execute(
        text("""
            SELECT
                count(*) AS total,
                count(*) FILTER (WHERE status != 'complete') AS incomplete
            FROM campaign_steps
            WHERE campaign_id = :campaign_id::uuid
        """),
        {"campaign_id": campaign_id},
    )
    row = result.one()
    return row.total > 0 and row.incomplete == 0


async def list_user_campaigns(
    db: AsyncSession,
    user_id: str,
    limit: int = 50,
    cursor_updated_at=None,
    cursor_id: str | None = None,
) -> list[dict[str, Any]]:
    """List campaigns owned by user_id, keyset-paginated by (updated_at DESC, id DESC)."""
    params: dict[str, Any] = {"uid": user_id, "lim": limit}
    cursor_clause = ""
    if cursor_updated_at is not None and cursor_id is not None:
        cursor_clause = "AND (updated_at, id) < (:cur_ts, :cur_id::uuid)"
        params["cur_ts"] = cursor_updated_at
        params["cur_id"] = cursor_id
    result = await db.execute(
        text(f"""
            SELECT id::text, session_id, created_by, target_smiles, status, plan,
                   created_at, updated_at
            FROM synthesis_campaigns
            WHERE created_by = :uid
              {cursor_clause}
            ORDER BY updated_at DESC, id DESC
            LIMIT :lim
        """),
        params,
    )
    return [dict(r._mapping) for r in result]


async def get_campaign_with_steps(
    db: AsyncSession,
    campaign_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Return a campaign plus its steps, owner-scoped."""
    campaign_result = await db.execute(
        text("""
            SELECT id::text, session_id, created_by, target_smiles, status, plan,
                   created_at, updated_at
            FROM synthesis_campaigns
            WHERE id = :cid::uuid AND created_by = :uid
        """),
        {"cid": campaign_id, "uid": user_id},
    )
    campaign_row = campaign_result.one_or_none()
    if campaign_row is None:
        return None
    steps_result = await db.execute(
        text("""
            SELECT id::text, step_idx, reaction_smiles, conditions, status,
                   retry_count, next_retry_at, result, updated_at
            FROM campaign_steps
            WHERE campaign_id = :cid::uuid
            ORDER BY step_idx
        """),
        {"cid": campaign_id},
    )
    return {
        "campaign": dict(campaign_row._mapping),
        "steps": [dict(r._mapping) for r in steps_result],
    }


async def reset_steps_for_retry(db: AsyncSession, step_ids: list[str]) -> None:
    """Reset eligible failed steps back to 'pending' so they get re-executed.

    Does NOT commit — caller manages the transaction.
    Only resets steps currently in 'failed' status to prevent double-reset.
    """
    if not step_ids:
        return
    await db.execute(
        text("""
            UPDATE campaign_steps
            SET status = 'pending', updated_at = now()
            WHERE id = ANY(:ids::uuid[])
              AND status = 'failed'
        """),
        {"ids": step_ids},
    )


async def cancel_campaign(
    db: AsyncSession,
    campaign_id: str,
    user_id: str,
) -> bool:
    """Transition a campaign to 'failed' (user cancellation). Owner-scoped."""
    async with db.begin():
        result = await db.execute(
            text("""
                UPDATE synthesis_campaigns
                SET status = 'failed', updated_at = now()
                WHERE id = :cid::uuid
                  AND created_by = :uid
                  AND status = ANY(:non_terminal)
                RETURNING id
            """),
            {"cid": campaign_id, "uid": user_id, "non_terminal": list(NON_TERMINAL_STATUSES)},
        )
        return result.one_or_none() is not None
