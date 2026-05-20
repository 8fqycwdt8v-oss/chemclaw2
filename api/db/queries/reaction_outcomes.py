"""Reaction outcome queries.

Outcomes are experimental results attached to a reaction — what was actually
tried, at what yield, and how it went. The process-gap-analyst sub-agent
reads them via list_outcomes_for_reaction and the outcome-aware variant of
find_similar_reactions to propose what's still untested for a given step.

Callers wrap mutations in ``async with session.begin()``; this module never
commits — matches the convention in the rest of ``api/db/queries/*``.
"""
from __future__ import annotations

import json
import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)

_VALID_SOURCES = frozenset(("eln", "manual", "campaign"))
_VALID_STATUSES = frozenset(("success", "partial", "fail", "inconclusive"))


async def insert_outcome(
    db: AsyncSession,
    *,
    reaction_id: str,
    source: str,
    status: str,
    created_by: str,
    campaign_step_id: str | None = None,
    eln_experiment_id: str | None = None,
    yield_pct: float | None = None,
    conditions_actual: dict[str, Any] | None = None,
    observations: str | None = None,
    failure_reason: str | None = None,
) -> tuple[str, bool]:
    """Insert a reaction outcome. Idempotent on ``eln_experiment_id``.

    Returns ``(outcome_id, already_existed)``. When the row already exists
    (matched on the partial unique index over ``eln_experiment_id``) the
    existing id is returned and the row is left untouched — callers can
    re-ingest the same ELN record without duplicating data.

    Idempotency is enforced via ``ON CONFLICT DO NOTHING`` against the
    partial unique index, then a fallback SELECT when RETURNING came back
    empty. That sequence is race-safe under concurrent ingest of the same
    ``eln_experiment_id``.
    """
    if source not in _VALID_SOURCES:
        raise ValueError(f"source must be one of {sorted(_VALID_SOURCES)!r}, got {source!r}")
    if status not in _VALID_STATUSES:
        raise ValueError(f"status must be one of {sorted(_VALID_STATUSES)!r}, got {status!r}")
    # CHECK constraint on yield_pct backs this up at the DB level; the
    # Python-side check gives a friendlier error before the SQL round-trip.
    if yield_pct is not None and not (0 <= yield_pct <= 100):
        raise ValueError(f"yield_pct must be between 0 and 100, got {yield_pct!r}")

    params: dict[str, Any] = {
        "reaction_id": reaction_id,
        "campaign_step_id": campaign_step_id,
        "eln_experiment_id": eln_experiment_id,
        "source": source,
        "status": status,
        "yield_pct": yield_pct,
        "conditions_actual": (
            json.dumps(conditions_actual) if conditions_actual is not None else None
        ),
        "observations": observations,
        "failure_reason": failure_reason,
        "created_by": created_by,
    }

    # ON CONFLICT against the partial unique index is race-safe: two
    # concurrent ingests of the same eln_experiment_id can't both win.
    # Uniformly CAST(:campaign_step_id AS uuid) — NULL casts cleanly to
    # NULL of the target type, avoiding asyncpg's AmbiguousParameterError
    # on bare IS-NULL parameters.
    result = await db.execute(
        text("""
            INSERT INTO reaction_outcomes (
                reaction_id, campaign_step_id, eln_experiment_id,
                source, status, yield_pct,
                conditions_actual, observations, failure_reason, created_by
            )
            VALUES (
                CAST(:reaction_id AS uuid),
                CAST(:campaign_step_id AS uuid),
                :eln_experiment_id,
                :source, :status, :yield_pct,
                CAST(:conditions_actual AS jsonb),
                :observations, :failure_reason, :created_by
            )
            ON CONFLICT (eln_experiment_id) WHERE eln_experiment_id IS NOT NULL
            DO NOTHING
            RETURNING id::text
        """),
        params,
    )
    row = result.first()
    if row is not None:
        return row[0], False

    # ON CONFLICT swallowed the insert — RETURNING is empty. Fetch the
    # winning row's id so the caller can still link to it.
    if eln_experiment_id is None:
        # No conflict target hit (no eln_experiment_id supplied) but
        # RETURNING came back empty anyway — shouldn't happen with a
        # valid INSERT. Surface as a programming error.
        raise RuntimeError("insert_outcome: INSERT returned no row and no conflict target applied")
    existing = await db.execute(
        text("""
            SELECT id::text FROM reaction_outcomes
            WHERE eln_experiment_id = :eln_experiment_id
        """),
        {"eln_experiment_id": eln_experiment_id},
    )
    existing_row = existing.first()
    if existing_row is None:
        raise RuntimeError(
            f"insert_outcome: ON CONFLICT fired for eln_experiment_id={eln_experiment_id!r} "
            "but the existing row is no longer visible"
        )
    return existing_row[0], True


async def list_outcomes_for_reaction(
    db: AsyncSession,
    reaction_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return outcomes for one reaction, newest first."""
    safe_limit = max(1, min(limit, 200))
    result = await db.execute(
        text("""
            SELECT id::text, reaction_id::text,
                   campaign_step_id::text AS campaign_step_id,
                   eln_experiment_id, source, status,
                   yield_pct, conditions_actual, observations, failure_reason,
                   recorded_at, created_by
            FROM reaction_outcomes
            WHERE reaction_id = CAST(:rid AS uuid)
            ORDER BY recorded_at DESC
            LIMIT :lim
        """),
        {"rid": reaction_id, "lim": safe_limit},
    )
    rows: list[dict[str, Any]] = []
    for r in result:
        d = dict(r._mapping)
        if d.get("yield_pct") is not None:
            d["yield_pct"] = float(d["yield_pct"])
        rows.append(d)
    return rows
