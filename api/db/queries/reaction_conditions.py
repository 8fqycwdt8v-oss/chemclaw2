"""Queries for `reaction_condition_predictions` — cache + feedback link.

Phase C of the reaction condition prediction rollout. Callers wrap
mutations in `async with session.begin()`; this module never commits.
"""
from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.queries._helpers import clamp_limit, row_to_dict, rows_to_dicts
from api.db.queries.fp_utils import bit_string_to_pg_bytes


async def get_cached_prediction(
    db: AsyncSession,
    rxn_smiles: str,
    model: str,
    reaction_id: str | None = None,
) -> dict[str, Any] | None:
    """Return the most recent prediction for (reaction_id, model) — or the
    most recent for `rxn_smiles` if no reaction_id is supplied. Both lookup
    paths are indexed.
    """
    if reaction_id is not None:
        result = await db.execute(
            text("""
                SELECT id::text, rxn_smiles, conditions, model, confidence,
                       source, used_in_step_id::text AS used_in_step_id,
                       created_at
                FROM reaction_condition_predictions
                WHERE reaction_id = CAST(:rid AS uuid) AND model = :model
                ORDER BY created_at DESC
                LIMIT 1
            """),
            {"rid": reaction_id, "model": model},
        )
    else:
        # Match the latest cached prediction for this (rxn_smiles, model),
        # regardless of whether the cached row is tied to a reaction_id.
        # SMILES equality is the cache key here — two callers asking
        # about the same reaction get the same prediction.
        result = await db.execute(
            text("""
                SELECT id::text, rxn_smiles, conditions, model, confidence,
                       source, used_in_step_id::text AS used_in_step_id,
                       created_at
                FROM reaction_condition_predictions
                WHERE rxn_smiles = :smiles AND model = :model
                ORDER BY created_at DESC
                LIMIT 1
            """),
            {"smiles": rxn_smiles, "model": model},
        )
    row = result.first()
    return row_to_dict(row)


async def insert_prediction(
    db: AsyncSession,
    rxn_smiles: str,
    conditions: dict[str, Any],
    model: str,
    source: str,
    created_by: str,
    confidence: float | None = None,
    reaction_id: str | None = None,
    drfp_bits: str | None = None,
) -> str:
    """Insert a new prediction row. The dedupe unique index lives on
    (reaction_id, model) where reaction_id IS NOT NULL — duplicate inserts
    against an existing (reaction_id, model) raise IntegrityError; callers
    should `get_cached_prediction` first or use ON CONFLICT.

    Returns the new prediction id as a string.
    """
    new_id = uuid.uuid4()
    # asyncpg's binary protocol refuses to bind a Python str to a
    # bit(2048) column even with CAST(); pack to bytes if provided.
    drfp_bytes = bit_string_to_pg_bytes(drfp_bits) if drfp_bits is not None else None
    result = await db.execute(
        text("""
            INSERT INTO reaction_condition_predictions (
                id, reaction_id, rxn_smiles, drfp_bits, conditions,
                model, confidence, source, created_by
            ) VALUES (
                CAST(:id AS uuid),
                CAST(:rid AS uuid),
                :smiles,
                CAST(:bits AS bit(2048)),
                CAST(:cond AS jsonb),
                :model,
                :conf,
                :source,
                :uid
            )
            ON CONFLICT (reaction_id, model)
                WHERE reaction_id IS NOT NULL
                DO UPDATE SET
                    conditions = EXCLUDED.conditions,
                    confidence = EXCLUDED.confidence,
                    source = EXCLUDED.source,
                    drfp_bits = EXCLUDED.drfp_bits,
                    created_by = EXCLUDED.created_by,
                    created_at = NOW()
            RETURNING id::text
        """),
        {
            "id": str(new_id),
            "rid": reaction_id,
            "smiles": rxn_smiles,
            "bits": drfp_bytes,
            "cond": _json_dumps(conditions),
            "model": model,
            "conf": confidence,
            "source": source,
            "uid": created_by,
        },
    )
    # ON CONFLICT DO UPDATE returns the existing row's id, not new_id —
    # surface whichever id the row actually has so callers can link to it.
    returned = result.scalar_one_or_none()
    return returned or str(new_id)


async def record_used_prediction(
    db: AsyncSession,
    prediction_id: str,
    step_id: str,
) -> bool:
    """Link a prediction to the campaign step that consumed it.

    Source-state predicate: only updates when used_in_step_id IS NULL,
    preventing a later step from clobbering the link. Returns True if
    the row was updated.
    """
    result = await db.execute(
        text("""
            UPDATE reaction_condition_predictions
            SET used_in_step_id = CAST(:sid AS uuid)
            WHERE id = CAST(:pid AS uuid) AND used_in_step_id IS NULL
        """),
        {"pid": prediction_id, "sid": step_id},
    )
    # SQLAlchemy 2.0 annotates Result.rowcount only on CursorResult.
    return (result.rowcount or 0) > 0  # type: ignore[attr-defined]


async def list_predictions_for_reaction(
    db: AsyncSession,
    reaction_id: str,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """Return all predictions for a reaction, newest first. Used by the
    agent to compare model output across runs and by feedback flows."""
    safe_limit = clamp_limit(limit, 100)
    result = await db.execute(
        text("""
            SELECT id::text, rxn_smiles, conditions, model, confidence,
                   source, used_in_step_id::text AS used_in_step_id,
                   created_at
            FROM reaction_condition_predictions
            WHERE reaction_id = CAST(:rid AS uuid)
            ORDER BY created_at DESC
            LIMIT :limit
        """),
        {"rid": reaction_id, "limit": safe_limit},
    )
    return rows_to_dicts(result)


def _json_dumps(value: dict[str, Any]) -> str:
    """Local helper — keep JSON encoding in one place so future changes
    (e.g. canonical key ordering for cache stability) land cleanly."""
    return json.dumps(value, sort_keys=True)
