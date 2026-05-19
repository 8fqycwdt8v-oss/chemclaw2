"""Campaign step queries — split out of `campaigns.py` to stay under the
~400-line per-module guideline (CLAUDE.md).

Step-level operations live here; campaign-level (status, listing, ownership)
operations stay in `campaigns.py`. The campaigns module re-exports the
public names from here for back-compat — external imports still work
either way.

Callers are responsible for transaction management. These functions do
NOT call db.commit() — wrap multi-step operations in `async with db.begin():`.
"""
from __future__ import annotations

import json
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

MAX_STEP_RETRIES = 3


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
            VALUES (CAST(:campaign_id AS uuid), :step_idx, :reaction_smiles, :conditions)
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
            WHERE campaign_id = CAST(:campaign_id AS uuid) AND status = 'pending'
            ORDER BY step_idx
        """),
        {"campaign_id": campaign_id},
    )
    return [dict(r._mapping) for r in result]


async def get_pending_steps_for_campaigns(
    db: AsyncSession,
    campaign_ids: list[str],
) -> dict[str, list[dict[str, Any]]]:
    """Batch variant: fetch pending steps for many campaigns in one query.

    Returns a dict mapping campaign_id (str) -> ordered list of step rows.
    Campaigns with no pending steps are present with an empty list so callers
    can iterate without per-campaign None checks.
    """
    out: dict[str, list[dict[str, Any]]] = {cid: [] for cid in campaign_ids}
    if not campaign_ids:
        return out
    result = await db.execute(
        text("""
            SELECT id::text, campaign_id::text AS cid, step_idx, reaction_smiles,
                   conditions, status, retry_count, next_retry_at
            FROM campaign_steps
            WHERE campaign_id = ANY(CAST(:ids AS uuid[])) AND status = 'pending'
            ORDER BY campaign_id, step_idx
        """),
        {"ids": campaign_ids},
    )
    for row in result:
        m = dict(row._mapping)
        cid = m.pop("cid")
        out.setdefault(cid, []).append(m)
    return out


async def all_complete_for_campaigns(
    db: AsyncSession,
    campaign_ids: list[str],
) -> dict[str, bool]:
    """Batch variant of all_steps_complete. Returns campaign_id → True iff
    every step is complete AND the campaign has at least one step.
    """
    out: dict[str, bool] = {cid: False for cid in campaign_ids}
    if not campaign_ids:
        return out
    result = await db.execute(
        text("""
            SELECT campaign_id::text AS cid,
                   count(*) AS total,
                   count(*) FILTER (WHERE status != 'complete') AS incomplete
            FROM campaign_steps
            WHERE campaign_id = ANY(CAST(:ids AS uuid[]))
            GROUP BY campaign_id
        """),
        {"ids": campaign_ids},
    )
    for row in result:
        out[row.cid] = row.total > 0 and row.incomplete == 0
    return out


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
            WHERE id = CAST(:id AS uuid)
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
                result     = CAST(:result AS jsonb),
                updated_at = now()
            WHERE id = CAST(:id AS uuid)
              AND status = 'pending'
        """),
        {"id": step_id, "result": json.dumps(result) if result is not None else None},
    )


async def all_steps_complete(db: AsyncSession, campaign_id: str) -> bool:
    """Return True if every step is complete AND the campaign has at least one step."""
    result = await db.execute(
        text("""
            SELECT
                count(*) AS total,
                count(*) FILTER (WHERE status != 'complete') AS incomplete
            FROM campaign_steps
            WHERE campaign_id = CAST(:campaign_id AS uuid)
        """),
        {"campaign_id": campaign_id},
    )
    row = result.one()
    return row.total > 0 and row.incomplete == 0


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
            WHERE id = ANY(CAST(:ids AS uuid[]))
              AND status = 'failed'
        """),
        {"ids": step_ids},
    )
