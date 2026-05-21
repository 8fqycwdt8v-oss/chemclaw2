"""Queries for `world_model_entries` — Kosmos-style structured persistent state.

The structured world model is the read/write surface a long-horizon agent
uses instead of cramming everything into the rolling LLM context. Each
entry is atomic so the agent can mark single observations superseded
without rewriting a blob.

Ownership: predicate on `created_by = :uid` for every UPDATE per the
CLAUDE.md §code-conventions rule. Cross-table ownership (i.e. that the
investigation also belongs to the caller) is enforced at the tool layer
before any of these helpers are called.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.queries.investigations import touch_investigation

logger = logging.getLogger(__name__)


_VALID_KINDS = {"fact", "assumption", "open_question", "evidence"}
_VALID_STATUSES = {"active", "superseded", "closed"}


async def add_world_model_entry(
    db: AsyncSession,
    investigation_id: str,
    user_id: str,
    kind: str,
    content: str,
    payload: dict[str, Any] | None = None,
    confidence: float | None = None,
) -> str:
    """Insert a new world-model entry. Returns the new id.

    Validates `kind`, `confidence` range, and bumps the parent
    investigation's `updated_at` in the same transaction so list ordering
    reflects recent activity.
    """
    if kind not in _VALID_KINDS:
        raise ValueError(f"kind must be one of {sorted(_VALID_KINDS)}, got {kind!r}")
    if confidence is not None and not (0.0 <= confidence <= 1.0):
        raise ValueError(f"confidence must be in [0, 1], got {confidence!r}")
    payload_json = json.dumps(payload or {})
    async with db.begin():
        result = await db.execute(
            text("""
                INSERT INTO world_model_entries
                    (investigation_id, kind, content, payload, confidence, created_by)
                VALUES (CAST(:iid AS uuid), :kind, :content, CAST(:payload AS jsonb),
                        :confidence, :uid)
                RETURNING id::text
            """),
            {
                "iid": investigation_id,
                "kind": kind,
                "content": content,
                "payload": payload_json,
                "confidence": confidence,
                "uid": user_id,
            },
        )
        entry_id = result.scalar_one()
        await touch_investigation(db, investigation_id)
        return entry_id


async def list_world_model_entries(
    db: AsyncSession,
    investigation_id: str,
    user_id: str,
    kind: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List entries for an investigation, owner-scoped."""
    if kind is not None and kind not in _VALID_KINDS:
        raise ValueError(f"kind must be one of {sorted(_VALID_KINDS)}, got {kind!r}")
    if status is not None and status not in _VALID_STATUSES:
        raise ValueError(f"status must be one of {sorted(_VALID_STATUSES)}, got {status!r}")
    safe_limit = min(max(1, limit), 500)
    params: dict[str, Any] = {"iid": investigation_id, "uid": user_id, "lim": safe_limit}
    kind_clause = ""
    if kind is not None:
        kind_clause = "AND kind = :kind"
        params["kind"] = kind
    status_clause = ""
    if status is not None:
        status_clause = "AND status = :status"
        params["status"] = status
    result = await db.execute(
        text(f"""
            SELECT id::text, kind, content, payload, confidence, status,
                   created_at, updated_at
            FROM world_model_entries
            WHERE investigation_id = CAST(:iid AS uuid)
              AND created_by = :uid
              {kind_clause}
              {status_clause}
            ORDER BY updated_at DESC, id DESC
            LIMIT :lim
        """),
        params,
    )
    return [dict(r._mapping) for r in result]


async def search_world_model_entries(
    db: AsyncSession,
    investigation_id: str,
    user_id: str,
    query: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """FTS over an investigation's world-model entries, owner-scoped."""
    safe_limit = min(max(1, limit), 100)
    result = await db.execute(
        text("""
            SELECT id::text, kind, content, payload, confidence, status,
                   ts_rank(to_tsvector('english', content),
                           plainto_tsquery('english', :q)) AS rank
            FROM world_model_entries
            WHERE investigation_id = CAST(:iid AS uuid)
              AND created_by = :uid
              AND to_tsvector('english', content)
                  @@ plainto_tsquery('english', :q)
            ORDER BY rank DESC
            LIMIT :lim
        """),
        {"iid": investigation_id, "uid": user_id, "q": query, "lim": safe_limit},
    )
    return [dict(r._mapping) for r in result]


async def update_world_model_entry_status(
    db: AsyncSession,
    entry_id: str,
    user_id: str,
    status: str,
) -> bool:
    """Mark an entry active / superseded / closed. Owner-scoped on
    `created_by`. Returns True if a row was updated."""
    if status not in _VALID_STATUSES:
        raise ValueError(f"status must be one of {sorted(_VALID_STATUSES)}, got {status!r}")
    async with db.begin():
        result = await db.execute(
            text("""
                UPDATE world_model_entries
                   SET status = :status,
                       updated_at = NOW()
                 WHERE id = CAST(:eid AS uuid)
                   AND created_by = :uid
            """),
            {"eid": entry_id, "uid": user_id, "status": status},
        )
        return result.rowcount > 0  # type: ignore[attr-defined]
