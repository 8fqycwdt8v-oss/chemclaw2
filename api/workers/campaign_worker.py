"""Campaign worker — asyncio background task that drives synthesis campaigns.

Responsibilities:
1. Step execution: Marks pending steps as complete (the actual chemistry
   computation hook is a stub — extend `_execute_step` for real logic).
2. Retry loop: Picks up failed steps whose backoff has elapsed, re-runs them.
3. Completion: When all steps in a campaign are complete, transitions the
   campaign to 'complete' and creates a wiki page summarising the results.

Run standalone:
    python -m api.workers.campaign_worker

Or mounted as a background task in the FastAPI lifespan (set
RUN_WORKER_IN_PROCESS=1 alongside fp_worker).
"""
from __future__ import annotations

import asyncio
import json
import logging
import sys
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 60

_in_flight = False


async def _execute_step(step: dict[str, Any]) -> dict[str, Any]:
    """Execute a campaign step and return the result.

    Currently a stub that marks the step successful. Extend this function
    to call fingerprint MCP servers, run retrosynthesis APIs, etc.
    """
    return {
        "reaction_smiles": step.get("reaction_smiles"),
        "conditions": step.get("conditions"),
        "executed": True,
    }


async def _create_campaign_wiki(
    db: AsyncSession,
    campaign: dict[str, Any],
) -> None:
    """Create a wiki page summarising a completed synthesis campaign."""
    from api.db.queries.wiki import upsert_wiki_page

    campaign_id = campaign["id"]
    target = campaign.get("target_smiles") or "unknown target"
    plan = campaign.get("plan") or {}

    title = f"Synthesis Campaign: {target[:60]}"
    slug = f"campaign-{campaign_id}"

    steps_text = ""
    if isinstance(plan, dict):
        steps = plan.get("steps") or []
        if steps:
            steps_text = "\n".join(
                f"  Step {i+1}: {s.get('reaction_smiles', 'N/A')} — {s.get('conditions', '')}"
                for i, s in enumerate(steps)
            )

    content_text = (
        f"# {title}\n\n"
        f"**Target:** {target}\n\n"
        f"**Campaign ID:** {campaign_id}\n\n"
        f"## Synthesis Steps\n{steps_text or '(no steps recorded)'}\n"
    )

    # embed_texts is imported lazily to avoid circular import at module load
    from api.routes.wiki import embed_texts

    try:
        await upsert_wiki_page(
            db,
            slug=slug,
            title=title,
            content={"type": "doc", "content": []},
            content_text=content_text,
            created_by=campaign.get("created_by", "system"),
            citations=[],
            embed_fn=embed_texts,
            project="synthesis-campaigns",
        )
        logger.info("campaign_wiki_created campaign=%s slug=%s", campaign_id, slug)
    except Exception:
        logger.exception("campaign_wiki_create_failed campaign=%s", campaign_id)


async def process_running_campaigns(db: AsyncSession) -> int:
    """Drive all running campaigns one step forward. Returns number of updates."""
    from api.db.queries.campaigns import (
        all_steps_complete,
        get_pending_campaign_steps,
        get_running_campaigns,
        mark_step_complete,
        mark_step_failed,
        update_campaign_status,
    )

    campaigns = await get_running_campaigns(db)
    updates = 0

    for campaign in campaigns:
        campaign_id = campaign["id"]
        pending_steps = await get_pending_campaign_steps(db, campaign_id)

        for step in pending_steps:
            step_id = step["id"]
            try:
                result = await _execute_step(step)
                async with db.begin():
                    await mark_step_complete(db, step_id, result)
                updates += 1
                logger.info("campaign_step_complete campaign=%s step=%s", campaign_id, step_id)
            except Exception as e:
                logger.warning("campaign_step_failed campaign=%s step=%s: %s", campaign_id, step_id, e)
                retry_count = step.get("retry_count", 0) + 1
                try:
                    async with db.begin():
                        await mark_step_failed(db, step_id, retry_count)
                except Exception:
                    logger.exception("campaign_step_fail_record_error step=%s", step_id)

        # After processing pending steps, check if everything is now complete.
        done = await all_steps_complete(db, campaign_id)
        if done:
            try:
                async with db.begin():
                    await update_campaign_status(db, campaign_id, "complete")
                logger.info("campaign_complete campaign=%s", campaign_id)
                await _create_campaign_wiki(db, campaign)
                updates += 1
            except Exception:
                logger.exception("campaign_complete_error campaign=%s", campaign_id)

    return updates


async def process_retry_steps(db: AsyncSession) -> int:
    """Reset eligible failed steps back to 'pending' so they get re-executed."""
    from api.db.queries.campaigns import get_steps_for_retry

    steps = await get_steps_for_retry(db)
    if not steps:
        return 0

    from sqlalchemy import text
    step_ids = [s["id"] for s in steps]
    try:
        async with db.begin():
            await db.execute(
                text("""
                    UPDATE campaign_steps
                    SET status = 'pending', updated_at = now()
                    WHERE id = ANY(:ids::uuid[])
                      AND status = 'failed'
                """),
                {"ids": step_ids},
            )
    except Exception:
        logger.exception("campaign_retry_reset_error")
        return 0

    logger.info("campaign_steps_reset_for_retry count=%d", len(steps))
    return len(steps)


async def run_worker(session_factory: async_sessionmaker[AsyncSession]) -> None:
    global _in_flight
    logger.info("campaign_worker_started")
    while True:
        if not _in_flight:
            _in_flight = True
            try:
                async with session_factory() as db:
                    retried = await process_retry_steps(db)
                    updated = await process_running_campaigns(db)
                if retried or updated:
                    logger.info(
                        "campaign_worker_cycle retried=%d updated=%d",
                        retried,
                        updated,
                    )
            except Exception:
                logger.exception("campaign_worker_cycle_error")
            finally:
                _in_flight = False
        await asyncio.sleep(POLL_INTERVAL_SECONDS)


if __name__ == "__main__":
    import os
    logging.basicConfig(level=logging.INFO)
    from api.db.connection import init_db, async_session_factory as factory
    init_db()
    if factory is None:
        raise RuntimeError("init_db() failed")
    asyncio.run(run_worker(factory))
