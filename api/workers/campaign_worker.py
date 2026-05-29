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
import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

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


_WIKI_RETRY_DELAYS_SEC = (1.0, 2.0, 4.0)


async def _create_campaign_wiki(
    campaign: dict[str, Any],
    session_factory: async_sessionmaker[AsyncSession],
) -> dict[str, Any]:
    """Create a wiki page summarising a completed synthesis campaign.

    Opens its own session — the slow embed call would otherwise hold the
    campaign-completion transaction open.

    Retries up to 3 times with exponential backoff against transient
    failures (embedding API rate-limit, brief DB blip). Returns
    `{"ok": bool, "error": str | None}` so the caller can distinguish
    "wiki created" from "logged and dropped" per CLAUDE.md observability
    rules. The slug is stable (`campaign-{id}`), so re-running this
    function for the same campaign is idempotent: `upsert_wiki_page`
    will no-op if the content hash hasn't changed.
    """
    from api.db.queries.wiki_write import upsert_wiki_page
    from api.embeddings import embed_texts

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

    last_error: str | None = None
    for attempt, delay in enumerate((0.0, *_WIKI_RETRY_DELAYS_SEC)):
        if delay > 0:
            await asyncio.sleep(delay)
        try:
            async with session_factory() as db:
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
            logger.info(
                "campaign_wiki_created campaign=%s slug=%s attempt=%d",
                campaign_id, slug, attempt + 1,
            )
            return {"ok": True, "error": None}
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "campaign_wiki_create_attempt_failed campaign=%s attempt=%d err=%s",
                campaign_id, attempt + 1, last_error,
            )
    logger.error(
        "campaign_wiki_create_exhausted_retries campaign=%s last_err=%s",
        campaign_id, last_error,
    )
    return {"ok": False, "error": last_error}


async def process_running_campaigns(
    db: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Drive all running campaigns one step forward. Returns number of updates."""
    from api.db.queries.campaigns import (
        all_complete_for_campaigns,
        get_pending_steps_for_campaigns,
        get_running_campaigns,
        mark_step_complete,
        mark_step_failed,
        system_advance_campaign,
    )
    from api.db.queries.reaction_conditions import (
        get_cached_prediction,
        record_used_prediction,
    )

    campaigns = await get_running_campaigns(db)
    updates = 0

    campaign_ids = [c["id"] for c in campaigns]
    pending_by_campaign = await get_pending_steps_for_campaigns(db, campaign_ids)

    for campaign in campaigns:
        campaign_id = campaign["id"]
        pending_steps = pending_by_campaign.get(campaign_id, [])

        for step in pending_steps:
            step_id = step["id"]
            try:
                result = await _execute_step(step)
                # Link a prior prediction (if any) to this step so the
                # predicted-vs-actual feedback loop has a join key. Best-effort:
                # if the prediction cache is empty for this reaction, skip
                # silently — the step still completes.
                prediction_id: str | None = None
                rxn_smiles = step.get("reaction_smiles")
                if rxn_smiles:
                    try:
                        cached = await get_cached_prediction(
                            db, rxn_smiles, model="rxn4chemistry:latest"
                        )
                        if cached:
                            prediction_id = cached["id"]
                            result = {**result, "prediction_id": prediction_id}
                    except Exception:
                        logger.exception("prediction_cache_lookup_failed step=%s", step_id)

                async with db.begin():
                    await mark_step_complete(db, step_id, result)
                    if prediction_id:
                        await record_used_prediction(db, prediction_id, step_id)
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

    # After processing every pending step across every campaign, check
    # completion in one batched query. Recompute because the mark_step_complete
    # calls above may have flipped some campaigns to all-complete.
    done_by_campaign = await all_complete_for_campaigns(db, campaign_ids)

    for campaign in campaigns:
        campaign_id = campaign["id"]
        if done_by_campaign.get(campaign_id):
            try:
                from api.db.queries.notifications import create_notification
                async with db.begin():
                    # Gate the notification on the actual transition: if the
                    # campaign was already 'complete' (re-observed this tick, or
                    # advanced by a concurrent worker), system_advance_campaign
                    # no-ops and returns False — without this guard the user
                    # would get a duplicate completion notification every time.
                    advanced = await system_advance_campaign(db, campaign_id, "complete")
                    created_by = campaign.get("created_by")
                    if advanced and created_by:
                        await create_notification(
                            db, created_by, "campaign_complete",
                            {"campaign_id": campaign_id,
                             "target_smiles": campaign.get("target_smiles")},
                        )
                if not advanced:
                    continue
                logger.info("campaign_complete campaign=%s", campaign_id)
                wiki_result = await _create_campaign_wiki(campaign, session_factory)
                if not wiki_result["ok"]:
                    # Status is already 'complete' and the user has been
                    # notified; the wiki is best-effort. A subsequent
                    # worker tick will retry via the backfill pass.
                    logger.error(
                        "campaign_complete_wiki_missing campaign=%s err=%s",
                        campaign_id, wiki_result["error"],
                    )
                updates += 1
            except Exception:
                logger.exception("campaign_complete_error campaign=%s", campaign_id)

    return updates


async def process_retry_steps(db: AsyncSession) -> int:
    """Reset eligible failed steps back to 'pending' so they get re-executed."""
    from api.db.queries.campaigns import get_steps_for_retry, reset_steps_for_retry

    steps = await get_steps_for_retry(db)
    if not steps:
        return 0

    step_ids = [s["id"] for s in steps]
    try:
        async with db.begin():
            await reset_steps_for_retry(db, step_ids)
    except Exception:
        logger.exception("campaign_retry_reset_error")
        return 0

    logger.info("campaign_steps_reset_for_retry count=%d", len(steps))
    return len(steps)


async def backfill_missing_campaign_wikis(
    db: AsyncSession,
    session_factory: async_sessionmaker[AsyncSession],
) -> int:
    """Re-create wiki pages for completed campaigns where the inline creation
    failed. Bounded to recently-completed campaigns by the underlying query.
    Returns the number of wikis successfully created on this pass.
    """
    from api.db.queries.campaigns import get_complete_campaigns_missing_wiki

    try:
        rows = await get_complete_campaigns_missing_wiki(db, limit=20)
    except Exception:
        logger.exception("campaign_wiki_backfill_query_error")
        return 0

    backfilled = 0
    for campaign in rows:
        result = await _create_campaign_wiki(campaign, session_factory)
        if result["ok"]:
            backfilled += 1
            logger.info("campaign_wiki_backfilled campaign=%s", campaign["id"])
    return backfilled


async def run_worker(session_factory: async_sessionmaker[AsyncSession]) -> None:
    global _in_flight
    logger.info("campaign_worker_started")
    _cycle = 0
    try:
        while True:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            if _in_flight:
                continue
            _in_flight = True
            try:
                async with session_factory() as db:
                    retried = await process_retry_steps(db)
                async with session_factory() as db:
                    updated = await process_running_campaigns(db, session_factory)
                # Backfill missing campaign wikis once every 5 cycles
                # (≈ once every 5 minutes at the default 60 s interval).
                backfilled = 0
                if _cycle % 5 == 0:
                    async with session_factory() as db:
                        backfilled = await backfill_missing_campaign_wikis(db, session_factory)
                # Sweep old rate_limit rows once every 60 cycles
                # (≈ once an hour at the default 60 s interval). The fixed-
                # window upsert never expires rows on its own, so unbounded
                # growth would slow the (key, window_start) lookup.
                swept = 0
                if _cycle % 60 == 0:
                    from api.db.queries.rate_limit import sweep_rate_limit_rows
                    try:
                        async with session_factory() as db:
                            swept = await sweep_rate_limit_rows(db)
                        if swept:
                            logger.info("rate_limit_rows_swept count=%d", swept)
                    except Exception:
                        logger.exception("rate_limit_sweep_error")
                if retried or updated or backfilled or swept:
                    logger.info(
                        "campaign_worker_cycle retried=%d updated=%d backfilled=%d swept=%d",
                        retried, updated, backfilled, swept,
                    )
            except Exception:
                logger.exception("campaign_worker_cycle_error")
            finally:
                _in_flight = False
                # Advance the cycle counter (and emit the heartbeat) in
                # `finally` so the modulo-gated periodic work (backfill %5,
                # rate-limit sweep %60) and the liveness heartbeat keep their
                # cadence even when a cycle raises — otherwise a persistently
                # failing cycle pins _cycle and starves the heartbeat while
                # re-running the heavy periodic passes every tick.
                _cycle += 1
                if _cycle % 10 == 0:
                    logger.info("campaign_worker_heartbeat cycle=%d", _cycle)
    except asyncio.CancelledError:
        logger.info("campaign_worker_shutdown")


if __name__ == "__main__":
    from api.observability.logging import configure_logging
    configure_logging()
    from api.db.connection import async_session_factory as factory
    from api.db.connection import init_db
    init_db()
    if factory is None:
        raise RuntimeError("init_db() failed")
    asyncio.run(run_worker(factory))
