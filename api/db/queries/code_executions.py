"""Persist + list `code_executions` — the agent-sandbox audit log."""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.queries._helpers import clamp_limit, validate_enum

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
    artifacts: list[dict[str, Any]] | None = None,
) -> str:
    """Persist one sandbox run. Returns the new row's id.

    Either `investigation_id` or `session_id` must be non-None (DB CHECK).

    When `investigation_id` is set, the INSERT is gated by an EXISTS check
    requiring `investigations.created_by = :uid` — so even if a future
    caller forgets the tool-layer ownership check, a stranger can't
    attach an execution to someone else's investigation. The same
    atomic statement raises ValueError when the investigation isn't
    owned (or doesn't exist).

    `artifacts` (Tier 3 §M): list of `{filename, mime, size_bytes, b64}`
    captured PNG figures from the sandbox tempdir. Stored as JSONB.
    """
    validate_enum(status, _VALID_STATUSES, "status")
    if investigation_id is None and session_id is None:
        raise ValueError("at least one of investigation_id, session_id must be set")
    params = {
        "iid": investigation_id,
        "sid": session_id,
        "code": code,
        "stdout": stdout,
        "stderr": stderr,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "status": status,
        "uid": created_by,
        "artifacts": json.dumps(artifacts or []),
    }
    async with db.begin():
        if investigation_id is not None:
            # EXISTS-gated INSERT: atomic; no SELECT-then-INSERT race.
            result = await db.execute(
                text("""
                    INSERT INTO code_executions
                        (investigation_id, session_id, code, stdout, stderr,
                         exit_code, duration_ms, status, artifacts, created_by)
                    SELECT CAST(:iid AS uuid), :sid, :code, :stdout, :stderr,
                           :exit_code, :duration_ms, :status,
                           CAST(:artifacts AS jsonb), :uid
                    WHERE EXISTS (
                        SELECT 1 FROM investigations
                         WHERE id = CAST(:iid AS uuid)
                           AND created_by = :uid
                    )
                    RETURNING id::text
                """),
                params,
            )
            row = result.first()
            if row is None:
                raise ValueError("investigation not found or not owned by created_by")
            return row[0]
        # Session-only path — no cross-table check needed.
        result = await db.execute(
            text("""
                INSERT INTO code_executions
                    (investigation_id, session_id, code, stdout, stderr,
                     exit_code, duration_ms, status, artifacts, created_by)
                VALUES (NULL, :sid, :code, :stdout, :stderr,
                        :exit_code, :duration_ms, :status,
                        CAST(:artifacts AS jsonb), :uid)
                RETURNING id::text
            """),
            params,
        )
        return result.scalar_one()


def _strip_artifact_payload(artifacts: Any) -> list[dict[str, Any]]:
    """Drop the b64 payload from each artefact dict — keeps list responses
    paginatable. Tolerant of stringified JSONB (older driver paths)."""
    if isinstance(artifacts, str):
        try:
            artifacts = json.loads(artifacts)
        except json.JSONDecodeError:
            logger.warning(
                "code_executions.artifacts stringified but not JSON-parseable",
            )
            return []
    if not isinstance(artifacts, list):
        if artifacts is not None:
            logger.warning(
                "code_executions.artifacts unexpected type: %s",
                type(artifacts).__name__,
            )
        return []
    out: list[dict[str, Any]] = []
    for a in artifacts:
        if not isinstance(a, dict):
            continue
        out.append({k: v for k, v in a.items() if k != "b64"})
    return out


async def list_executions(
    db: AsyncSession,
    user_id: str,
    investigation_id: str | None = None,
    session_id: str | None = None,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """List executions for the caller, optionally filtered by investigation
    or chat session. Owner-scoped on `created_by`.

    `artifacts` returned with the b64 payload stripped — keeps list
    responses small. Use `get_execution(id)` for the full payload."""
    safe_limit = clamp_limit(limit, 100)
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
                   stdout, stderr, exit_code, duration_ms, status,
                   artifacts, created_at
            FROM code_executions
            WHERE {where}
            ORDER BY created_at DESC, id DESC
            LIMIT :lim
        """),
        params,
    )
    rows = []
    for r in result:
        d = dict(r._mapping)
        d["artifacts"] = _strip_artifact_payload(d.get("artifacts"))
        rows.append(d)
    return rows


async def attach_artifact_critique(
    db: AsyncSession,
    execution_id: str,
    user_id: str,
    *,
    filename: str,
    art_hash: str,
    critique: dict[str, Any],
) -> bool:
    """Attach a VLM critique to one artifact in a run, owner-scoped.

    Read-modify-write of the `artifacts` JSONB inside a single
    transaction: the matching artifact dict gains a `critique` key
    `{**critique, "hash": art_hash}` so a later re-critique of the same
    unchanged figure (same byte hash) can return the cached result for
    free. Returns False when the execution isn't owned by the caller or
    no artifact matches `filename`.
    """
    async with db.begin():
        result = await db.execute(
            text("""
                SELECT artifacts FROM code_executions
                 WHERE id = CAST(:eid AS uuid)
                   AND created_by = :uid
                 FOR UPDATE
            """),
            {"eid": execution_id, "uid": user_id},
        )
        row = result.one_or_none()
        if row is None:
            return False
        artifacts = row._mapping["artifacts"]
        if isinstance(artifacts, str):
            try:
                artifacts = json.loads(artifacts)
            except json.JSONDecodeError:
                logger.warning(
                    "code_executions(id=%s).artifacts not JSON-parseable on "
                    "critique attach", execution_id,
                )
                return False
        if not isinstance(artifacts, list):
            return False
        matched = False
        for a in artifacts:
            if isinstance(a, dict) and a.get("filename") == filename:
                a["critique"] = {**critique, "hash": art_hash}
                matched = True
                break
        if not matched:
            return False
        await db.execute(
            text("""
                UPDATE code_executions
                   SET artifacts = CAST(:artifacts AS jsonb)
                 WHERE id = CAST(:eid AS uuid)
                   AND created_by = :uid
            """),
            {"artifacts": json.dumps(artifacts), "eid": execution_id, "uid": user_id},
        )
        return True


async def get_execution(
    db: AsyncSession,
    execution_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Owner-scoped single-row fetch. Includes full artefact payloads
    (b64). Pair with `list_executions` for paginated overview."""
    result = await db.execute(
        text("""
            SELECT id::text, investigation_id::text, session_id, code,
                   stdout, stderr, exit_code, duration_ms, status,
                   artifacts, created_at
            FROM code_executions
            WHERE id = CAST(:eid AS uuid)
              AND created_by = :uid
        """),
        {"eid": execution_id, "uid": user_id},
    )
    row = result.one_or_none()
    if row is None:
        return None
    d = dict(row._mapping)
    # JSONB columns come back as parsed Python lists most of the time,
    # but stringified depending on the driver/pgvector combo. Normalise.
    artifacts = d.get("artifacts")
    if isinstance(artifacts, str):
        try:
            d["artifacts"] = json.loads(artifacts)
        except json.JSONDecodeError:
            logger.warning(
                "code_executions(id=%s).artifacts not JSON-parseable; "
                "returning empty list", execution_id,
            )
            d["artifacts"] = []
    elif not isinstance(artifacts, list):
        d["artifacts"] = []
    return d
