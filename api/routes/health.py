import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.connection import get_db
from api.db.queries.compounds import count_pending_fingerprints

logger = logging.getLogger(__name__)
router = APIRouter()

# Backlog above which `/readiness` reports degraded — workers are
# falling behind enough that new requests shouldn't be routed here.
_BACKLOG_DEGRADED_THRESHOLD = 5000


@router.get("/api/health")
async def health():
    """Liveness — process is up and the event loop is responsive.

    Intentionally does NOT touch the database. A DB blip should NOT
    cause the orchestrator to restart the process; that's what the
    `/api/readiness` probe is for (signalling drain instead of restart).
    """
    return JSONResponse(content={"ok": True}, status_code=200)


@router.get("/api/readiness")
async def readiness(db: AsyncSession = Depends(get_db)):
    """Readiness — the process can accept new traffic.

    Distinct from /health so load balancers can drain (stop sending new
    requests) without forcing a process restart when a transient downstream
    dependency hiccups.

    Returns 503 when:
      * the database is unreachable
      * the fingerprint backlog crosses the degraded threshold (workers
        are falling behind faster than they can catch up — surfacing this
        as 'not ready' lets the platform stop adding load).

    Body always includes the diagnostic data so dashboards can chart it
    independent of the status code.
    """
    db_ok = False
    pending_compounds = 0
    pending_reactions = 0
    try:
        # Trivial round-trip to confirm the connection is usable. Cheaper
        # than the count query below, so we run it first and short-circuit
        # the response if it fails.
        await db.execute(text("SELECT 1"))
        counts = await count_pending_fingerprints(db)
        pending_compounds = counts["pending_compounds"]
        pending_reactions = counts["pending_reactions"]
        db_ok = True
    except Exception:
        logger.warning("readiness_db_check_failed", exc_info=True)

    backlog = pending_compounds + pending_reactions
    ready = db_ok and backlog < _BACKLOG_DEGRADED_THRESHOLD
    body = {
        "ready": ready,
        "db": db_ok,
        "fingerprint_backlog": {
            "compounds": pending_compounds,
            "reactions": pending_reactions,
        },
        "worker_warn": backlog > 500,
        "backlog_degraded": backlog >= _BACKLOG_DEGRADED_THRESHOLD,
    }
    return JSONResponse(content=body, status_code=200 if ready else 503)
