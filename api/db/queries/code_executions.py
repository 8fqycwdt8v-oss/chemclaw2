"""Persist + list `code_executions` — the agent-sandbox audit log."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


_VALID_STATUSES = {"completed", "timeout", "error", "killed"}


async def insert_execution(
    db: AsyncSession,
    *,
    code: str,
    stdout: str,
    stderr: str,
    exit_code: int,
    duration_ms: int,
    status: str,
    created_by: str,
    investigation_id: str | None = None,
    session_id: str | None = None,
) -> str:
    """Persist one sandbox run. Returns the new row's id.

    Either `investigation_id` or `session_id` must be non-None (DB CHECK).
    """
    if status not in _VALID_STATUSES:
        raise ValueError(f"status must be one of {sorted(_VALID_STATUSES)}, got {status!r}")
    if investigation_id is None and session_id is None:
        raise ValueError("at least one of investigation_id, session_id must be set")
    async with db.begin():
        result = await db.execute(
            text("""
                INSERT INTO code_executions
                    (investigation_id, session_id, code, stdout, stderr,
                     exit_code, duration_ms, status, created_by)
                VALUES (CAST(:iid AS uuid), :sid, :code, :stdout, :stderr,
                        :exit_code, :duration_ms, :status, :uid)
                RETURNING id::text
            """),
            {
                "iid": investigation_id,
                "sid": session_id,
                "code": code,
                "stdout": stdout,
                "stderr": stderr,
                "exit_code": exit_code,
                "duration_ms": duration_ms,
                "status": status,
                "uid": created_by,
            },
        )
        return result.scalar_one()


async def list_executions(
    db: AsyncSession,
    user_id: str,
    investigation_id: str | None = None,
    session_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List executions for the caller, optionally filtered by investigation
    or chat session. Owner-scoped on `created_by`."""
    safe_limit = min(max(1, limit), 100)
    params: dict[str, Any] = {"uid": user_id, "lim": safe_limit}
    clauses = ["created_by = :uid"]
    if investigation_id is not None:
        clauses.append("investigation_id = CAST(:iid AS uuid)")
        params["iid"] = investigation_id
    if session_id is not None:
        clauses.append("session_id = :sid")
        params["sid"] = session_id
    where = " AND ".join(clauses)
    result = await db.execute(
        text(f"""
            SELECT id::text, investigation_id::text, session_id, code,
                   stdout, stderr, exit_code, duration_ms, status, created_at
            FROM code_executions
            WHERE {where}
            ORDER BY created_at DESC, id DESC
            LIMIT :lim
        """),
        params,
    )
    return [dict(r._mapping) for r in result]


async def get_execution(
    db: AsyncSession,
    execution_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Owner-scoped single-row fetch."""
    result = await db.execute(
        text("""
            SELECT id::text, investigation_id::text, session_id, code,
                   stdout, stderr, exit_code, duration_ms, status, created_at
            FROM code_executions
            WHERE id = CAST(:eid AS uuid)
              AND created_by = :uid
        """),
        {"eid": execution_id, "uid": user_id},
    )
    row = result.one_or_none()
    return dict(row._mapping) if row else None
