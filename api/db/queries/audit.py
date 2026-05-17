"""Audit trail queries."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


async def list_overrides(
    db: AsyncSession,
    user_id: str | None = None,
    session_id: str | None = None,
    gate_name: str | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    """Return agent override records, newest first.

    Filters on user_id, session_id, and/or gate_name when provided.
    prompt_hash is never returned to callers.
    """
    safe_limit = max(1, min(limit, 200))
    params: dict[str, Any] = {"lim": safe_limit}

    clauses: list[str] = []
    if user_id is not None:
        clauses.append("user_id = :uid")
        params["uid"] = user_id
    if session_id is not None:
        clauses.append("session_id = :sid")
        params["sid"] = session_id
    if gate_name is not None:
        clauses.append("gate_name = :gate")
        params["gate"] = gate_name

    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""

    result = await db.execute(
        text(f"""
            SELECT id::text, session_id, user_id, gate_name, justification, created_at
            FROM agent_overrides
            {where}
            ORDER BY created_at DESC
            LIMIT :lim
        """),
        params,
    )
    return [dict(r._mapping) for r in result]


async def get_session_replay(
    db: AsyncSession,
    session_id: str,
    project_key: str,
) -> list[dict[str, Any]]:
    """Return all session entries for a given session_id + project_key, in insertion order.

    Returns an empty list when no matching session rows exist.
    """
    result = await db.execute(
        text("""
            SELECT entries
            FROM agent_sessions
            WHERE project_key = :pk
              AND session_id  = :sid
            ORDER BY insert_seq
        """),
        {"pk": project_key, "sid": session_id},
    )
    rows = result.fetchall()
    out: list[dict[str, Any]] = []
    for (entries,) in rows:
        if isinstance(entries, list):
            out.extend(entries)
    return out
