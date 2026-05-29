"""SharePoint/OneDrive drive-sync worker.

Wall-clock-gated (~12 h, `SYNC_INTERVAL_HOURS`) pull of changed files from a
Microsoft Graph drive via the delta query, feeding each through the shared
ingest pipeline — searchable chunks + a needs-review wiki draft + knowledge-
graph facts/hypotheses. The delta cursor stored in `drive_sync_state` makes
every run incremental, so steady-state syncs only touch what changed.

Run standalone:
    python -m api.workers.sync_worker

Or mounted in the FastAPI lifespan when RUN_WORKER_IN_PROCESS=1 and the
MSGRAPH_* env vars are set (see api/main.py).
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.db.queries.drive_sync import (
    get_sync_state,
    release_drive_lock,
    save_sync_state,
    try_acquire_drive_lock,
)
from api.db.queries.investigations import get_or_create_corpus_investigation
from api.integrations.extractors import resolve_content_type
from api.integrations.ingest import ingest_document
from api.integrations.sharepoint.graph_client import (
    GraphConfig,
    acquire_token,
    delta,
    download_by_url,
    select_changed_files,
)

logger = logging.getLogger(__name__)

POLL_INTERVAL_SECONDS = 300
_MAX_FILE_BYTES = 10 * 1024 * 1024  # mirror the upload route's cap
# System owner for drive-sync-created investigations + world-model entries.
_SYNC_USER = "drive-sync"


def _sync_interval_hours() -> float:
    try:
        return float(os.environ.get("SYNC_INTERVAL_HOURS", "12"))
    except ValueError:
        logger.warning("invalid SYNC_INTERVAL_HOURS; defaulting to 12")
        return 12.0


def _sync_due(last_synced_at: datetime | None, interval_hours: float) -> bool:
    """True if a drive has never synced or its last sync is older than the
    interval. Wall-clock based, so worker restarts don't reset the cadence."""
    if last_synced_at is None:
        return True
    if last_synced_at.tzinfo is None:
        last_synced_at = last_synced_at.replace(tzinfo=UTC)
    age = datetime.now(UTC) - last_synced_at
    return age.total_seconds() >= interval_hours * 3600


async def run_sync_once(session_factory: async_sessionmaker[AsyncSession]) -> dict[str, Any]:
    """Run a single delta sync of the configured drive. Returns a summary dict.

    No-ops with status 'skipped' when MSGRAPH_* isn't configured. On a Graph
    failure it records the error in drive_sync_state (preserving the prior
    cursor) and returns status 'error' without raising. Per-file failures are
    logged and counted; one bad file never aborts the batch.
    """
    config = GraphConfig.from_env()
    if config is None:
        return {"status": "skipped", "reason": "not configured"}

    async with session_factory() as db:
        investigation_id = await get_or_create_corpus_investigation(
            db,
            title=f"SharePoint drive {config.drive_id}",
            objective="Knowledge extracted from the synced SharePoint/OneDrive drive.",
            created_by=_SYNC_USER,
        )

    async with session_factory() as db:
        state = await get_sync_state(db, config.drive_id)
    delta_link = state["delta_token"] if state else None

    try:
        token = await acquire_token(config)
        items, new_link = await delta(token, config.drive_id, delta_link=delta_link)
    except Exception as exc:
        logger.exception("drive_sync_delta_failed drive=%s", config.drive_id)
        async with session_factory() as db:
            await save_sync_state(
                db, config.drive_id, status="error", error=type(exc).__name__
            )
        return {"status": "error", "stage": "delta"}

    files, deleted = select_changed_files(items)
    ingested = skipped = failed = 0
    for item in files:
        name = item.get("name") or ""
        content_type = resolve_content_type(name, (item.get("file") or {}).get("mimeType"))
        url = item.get("@microsoft.graph.downloadUrl")
        if content_type is None or not url:
            skipped += 1
            continue
        if (item.get("size") or 0) > _MAX_FILE_BYTES:
            logger.info("drive_sync_skip_oversize name=%s size=%s", name, item.get("size"))
            skipped += 1
            continue
        try:
            content = await download_by_url(url)
            if len(content) > _MAX_FILE_BYTES:
                logger.info("drive_sync_skip_oversize_after_download name=%s", name)
                skipped += 1
                continue
            async with session_factory() as db:
                await ingest_document(
                    db,
                    content=content,
                    filename=name,
                    content_type=content_type,
                    user_id=_SYNC_USER,
                    extract="full",
                    extract_kg=True,
                    investigation_id=investigation_id,
                )
            ingested += 1
        except Exception:
            logger.exception("drive_sync_ingest_failed name=%s", name)
            failed += 1

    async with session_factory() as db:
        await save_sync_state(db, config.drive_id, status="ok", delta_token=new_link)

    if deleted:
        # Deletions are left in place for now (curator review) rather than
        # tombstoned — see docs/plans/sharepoint-drive-sync.md open question.
        logger.info(
            "drive_sync_deletions drive=%s count=%d (left in place)",
            config.drive_id, len(deleted),
        )
    logger.info(
        "drive_sync_complete drive=%s files=%d ingested=%d skipped=%d failed=%d deleted=%d",
        config.drive_id, len(files), ingested, skipped, failed, len(deleted),
    )
    return {
        "status": "ok",
        "files": len(files),
        "ingested": ingested,
        "skipped": skipped,
        "failed": failed,
        "deleted": len(deleted),
    }


async def run_worker(session_factory: async_sessionmaker[AsyncSession]) -> None:
    """Poll loop: every POLL_INTERVAL_SECONDS, sync the drive if it's due.

    Holds a per-drive advisory lock for the duration of a sync so multiple
    instances don't sync the same drive concurrently.
    """
    logger.info("sync_worker_started interval_hours=%s", _sync_interval_hours())
    _cycle = 0
    _in_flight = False
    try:
        while True:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            if _in_flight:
                continue
            _in_flight = True
            try:
                config = GraphConfig.from_env()
                if config is None:
                    continue
                async with session_factory() as db:
                    state = await get_sync_state(db, config.drive_id)
                last = state["last_synced_at"] if state else None
                if not _sync_due(last, _sync_interval_hours()):
                    continue
                # Hold the advisory lock on a dedicated connection for the whole
                # sync; release in finally even if run_sync_once raises.
                async with session_factory() as lock_db:
                    if not await try_acquire_drive_lock(lock_db, config.drive_id):
                        logger.info("sync_worker_lock_busy drive=%s", config.drive_id)
                        continue
                    try:
                        result = await run_sync_once(session_factory)
                        logger.info("sync_worker_cycle %s", result)
                    finally:
                        await release_drive_lock(lock_db, config.drive_id)
            except Exception:
                logger.exception("sync_worker_cycle_error")
            finally:
                _in_flight = False
                _cycle += 1
                if _cycle % 10 == 0:
                    logger.info("sync_worker_heartbeat cycle=%d", _cycle)
    except asyncio.CancelledError:
        logger.info("sync_worker_shutdown")


if __name__ == "__main__":
    from api.observability.logging import configure_logging
    configure_logging()
    from api.db.connection import async_session_factory as factory
    from api.db.connection import init_db

    init_db()
    if factory is None:
        raise SystemExit("Database not initialised")
    asyncio.run(run_worker(factory))
