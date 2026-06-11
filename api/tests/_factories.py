"""Shared async row factories for DB-integration tests.

`new_campaign` was copy-pasted in three test modules (test_optimization,
test_campaigns_owner_scope, test_curator_inbox — the last never even called
its copy). Centralise it so the insert shape lives in one place; callers pass
`status` for the lifecycle state they need.
"""
from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def new_campaign(
    session_factory: async_sessionmaker[AsyncSession],
    user_id: str,
    status: str = "planning",
    target: str = "CCO",
) -> str:
    """Insert a minimal synthesis_campaigns row owned by `user_id`. Returns the id."""
    async with session_factory() as db, db.begin():
        result = await db.execute(
            text("""
                    INSERT INTO synthesis_campaigns
                        (created_by, session_id, target_smiles, status)
                    VALUES (:uid, :sid, :target, :status)
                    RETURNING id::text
                """),
            {
                "uid": user_id,
                "sid": f"sess-{uuid.uuid4().hex[:12]}",
                "target": target,
                "status": status,
            },
        )
        return result.scalar_one()
