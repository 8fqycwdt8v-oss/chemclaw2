"""Campaign queries — campaign-level operations (status, listing, ownership).

Step-level operations (add_campaign_step, mark_step_*, all_*_for_campaigns,
get_steps_for_retry, reset_steps_for_retry, all_steps_complete) live in
`campaign_steps.py` per the ~400-LOC split rule. They are re-exported
here so existing imports continue to work — prefer importing from
`campaign_steps` directly in new code.

Callers are responsible for transaction management. These functions do NOT
call db.commit() — wrap multi-step operations in `async with db.begin():`.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

# Re-exports from campaign_steps for back-compat.
from api.db.queries.campaign_steps import (  # noqa: F401
    MAX_STEP_RETRIES,
    add_campaign_step,
    all_complete_for_campaigns,
    all_steps_complete,
    approve_step,
    get_pending_campaign_steps,
    get_pending_steps_for_campaigns,
    get_steps_for_retry,
    list_steps_awaiting_approval,
    mark_step_complete,
    mark_step_failed,
    reject_step,
    reset_steps_for_retry,
)

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
) -> bool:
    """Update campaign status for the campaign owner.

    Includes `created_by = :user_id` in the WHERE clause so a user can only
    advance their own campaigns. For system/worker calls use
    `system_advance_campaign()`.

    Returns True iff a row was updated. A False result means the campaign
    does not exist, is not owned by `user_id`, or is already terminal — the
    caller MUST treat that as a failed ownership/state check and not proceed
    with dependent writes (e.g. inserting steps). A bare-None return made the
    zero-row case indistinguishable from success.

    Does NOT commit — caller manages the transaction.
    Source-state predicate excludes terminal statuses to prevent
    double-transitions.
    """
    plan_clause = ", plan = CAST(:plan AS jsonb)" if plan is not None else ""
    params: dict[str, Any] = {
        "id": campaign_id,
        "user_id": user_id,
        "status": status,
        "statuses": list(NON_TERMINAL_STATUSES),
    }
    if plan is not None:
        params["plan"] = json.dumps(plan)
    result = await db.execute(
        text(f"""
            UPDATE synthesis_campaigns
            SET status = :status, updated_at = now(){plan_clause}
            WHERE id = CAST(:id AS uuid)
              AND created_by = :user_id
              AND status = ANY(:statuses)
            RETURNING id
        """),
        params,
    )
    return result.one_or_none() is not None


async def system_advance_campaign(
    db: AsyncSession,
    campaign_id: str,
    status: str,
    plan: dict[str, Any] | None = None,
) -> bool:
    """Advance a campaign status from a system/worker context (no ownership check).

    Only callable from background workers. Never expose via HTTP routes.

    Returns True iff a row was updated. The source-state predicate excludes
    terminal statuses, so a campaign already `complete`/`failed` yields False
    — callers MUST gate dependent side effects (completion notification, wiki
    creation) on a True result so a re-observed campaign doesn't re-fire them.

    Does NOT commit — caller manages the transaction.
    """
    plan_clause = ", plan = CAST(:plan AS jsonb)" if plan is not None else ""
    params: dict[str, Any] = {
        "id": campaign_id,
        "status": status,
        "statuses": list(NON_TERMINAL_STATUSES),
    }
    if plan is not None:
        params["plan"] = json.dumps(plan)
    result = await db.execute(
        text(f"""
            UPDATE synthesis_campaigns
            SET status = :status, updated_at = now(){plan_clause}
            WHERE id = CAST(:id AS uuid)
              AND status = ANY(:statuses)
            RETURNING id
        """),
        params,
    )
    return result.one_or_none() is not None


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


async def get_complete_campaigns_missing_wiki(
    db: AsyncSession, limit: int = 20
) -> list[dict[str, Any]]:
    """Return recently-completed campaigns whose summary wiki page is missing.

    The wiki creation runs outside the completion transaction (slow embed
    call), so a failure there leaves the campaign in `complete` without a
    wiki. This query is the input for a backfill pass in the worker.

    Scoped to the last 24 h so the worker doesn't endlessly retry
    historically-failed campaigns. Operators should backfill anything
    older by running the worker tick manually after fixing the underlying
    failure (e.g. embedding API outage).
    """
    result = await db.execute(
        text("""
            SELECT c.id::text, c.session_id, c.created_by,
                   c.target_smiles, c.plan, c.updated_at
            FROM synthesis_campaigns c
            LEFT JOIN wiki_pages w ON w.slug = 'campaign-' || c.id::text
            WHERE c.status = 'complete'
              AND c.updated_at >= now() - interval '24 hours'
              AND w.id IS NULL
            ORDER BY c.updated_at DESC
            LIMIT :lim
        """),
        {"lim": limit},
    )
    return [dict(r._mapping) for r in result]


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
        cursor_clause = "AND (updated_at, id) < (:cur_ts, CAST(:cur_id AS uuid))"
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
            WHERE id = CAST(:cid AS uuid) AND created_by = :uid
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
            WHERE campaign_id = CAST(:cid AS uuid)
            ORDER BY step_idx
        """),
        {"cid": campaign_id},
    )
    return {
        "campaign": dict(campaign_row._mapping),
        "steps": [dict(r._mapping) for r in steps_result],
    }


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
                WHERE id = CAST(:cid AS uuid)
                  AND created_by = :uid
                  AND status = ANY(:non_terminal)
                RETURNING id
            """),
            {"cid": campaign_id, "uid": user_id, "non_terminal": list(NON_TERMINAL_STATUSES)},
        )
        return result.one_or_none() is not None
