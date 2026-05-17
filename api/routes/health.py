import logging

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from api.db.connection import get_db

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/api/health")
async def health(db: AsyncSession = Depends(get_db)):
    db_ok = False
    pending_compounds = 0
    pending_reactions = 0
    try:
        result = await db.execute(text("""
            SELECT
              (SELECT count(*) FROM compounds WHERE morgan_fp IS NULL)::int AS pending_compounds,
              (SELECT count(*) FROM reactions  WHERE drfp      IS NULL)::int AS pending_reactions
        """))
        row = result.one()
        pending_compounds = row.pending_compounds
        pending_reactions = row.pending_reactions
        db_ok = True
    except Exception:
        logger.warning("health_db_check_failed", exc_info=True)

    return {
        "ok": db_ok,
        "db": db_ok,
        "fingerprint_backlog": {
            "compounds": pending_compounds,
            "reactions": pending_reactions,
        },
        "worker_warn": (pending_compounds + pending_reactions) > 500,
    }
