import logging

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.connection import get_db
from api.db.queries.compounds import count_pending_fingerprints

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/health")
async def health(db: AsyncSession = Depends(get_db)):
    db_ok = False
    pending_compounds = 0
    pending_reactions = 0
    try:
        counts = await count_pending_fingerprints(db)
        pending_compounds = counts["pending_compounds"]
        pending_reactions = counts["pending_reactions"]
        db_ok = True
    except Exception:
        logger.warning("health_db_check_failed", exc_info=True)

    body = {
        "ok": db_ok,
        "db": db_ok,
        "fingerprint_backlog": {
            "compounds": pending_compounds,
            "reactions": pending_reactions,
        },
        "worker_warn": (pending_compounds + pending_reactions) > 500,
    }
    return JSONResponse(content=body, status_code=200 if db_ok else 503)
