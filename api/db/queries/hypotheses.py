"""Hypothesis lifecycle + tournament ranking — Google Co-Scientist primitives.

`hypotheses` carries the claim + Elo rating; `hypothesis_rankings` is the
append-only audit log of pairwise judgments. Recording a ranking
eager-updates both contestants' ratings in the same transaction so the
hot read path (rank-ordered list) doesn't need to recompute.

Elo math (`elo_update`) is pure and unit-tested separately.
"""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from api.db.queries.investigations import touch_investigation

logger = logging.getLogger(__name__)


_VALID_HYPOTHESIS_STATUSES = {"proposed", "ranked", "refined", "retired"}
_VALID_WINNERS = {"a", "b", "tie"}

# Standard Elo K-factor. 32 is the FIDE blitz default — meaningfully
# responsive without being unstable. Higher K = ratings move faster.
DEFAULT_K_FACTOR = 32.0


def elo_update(
    rating_a: float,
    rating_b: float,
    winner: str,
    k: float = DEFAULT_K_FACTOR,
) -> tuple[float, float]:
    """Standard Elo update. Pure function; no I/O.

    `winner` is 'a', 'b', or 'tie'. Returns (new_rating_a, new_rating_b).
    Implements the textbook formula:
        expected_a = 1 / (1 + 10 ** ((rating_b - rating_a) / 400))
        score_a    = 1 / 0.5 / 0 for a-wins / tie / b-wins
        new_a      = rating_a + K * (score_a - expected_a)
        new_b      = rating_b + K * ((1 - score_a) - (1 - expected_a))

    Symmetric: a's gain equals b's loss. K-factor controls responsiveness
    (default 32 from FIDE blitz; pass a smaller value when many low-quality
    rounds are noise-dominating real signal).
    """
    if winner not in _VALID_WINNERS:
        raise ValueError(f"winner must be one of {sorted(_VALID_WINNERS)}, got {winner!r}")
    if k <= 0:
        raise ValueError(f"k must be > 0, got {k!r}")
    expected_a = 1.0 / (1.0 + 10.0 ** ((rating_b - rating_a) / 400.0))
    expected_b = 1.0 - expected_a
    if winner == "a":
        score_a, score_b = 1.0, 0.0
    elif winner == "b":
        score_a, score_b = 0.0, 1.0
    else:  # tie
        score_a, score_b = 0.5, 0.5
    new_a = rating_a + k * (score_a - expected_a)
    new_b = rating_b + k * (score_b - expected_b)
    return new_a, new_b


# ── CRUD ──────────────────────────────────────────────────────────────────────


async def create_hypothesis(
    db: AsyncSession,
    investigation_id: str,
    user_id: str,
    statement: str,
    rationale: str | None = None,
    parent_id: str | None = None,
) -> str:
    """Insert a new hypothesis. Returns the new id.

    `parent_id`, when set, establishes the Evolution chain. The parent
    must already exist *and* be owned by the same user (enforced via the
    `created_by = :uid` predicate inside the EXISTS subquery so passing a
    stranger's hypothesis id silently fails — same fail-closed pattern as
    other cross-row references).
    """
    async with db.begin():
        if parent_id is not None:
            # Verify parent is owned by the caller before we attach to it.
            owned = await db.execute(
                text("""
                    SELECT 1 FROM hypotheses
                     WHERE id = CAST(:pid AS uuid)
                       AND created_by = :uid
                """),
                {"pid": parent_id, "uid": user_id},
            )
            if owned.first() is None:
                raise ValueError("parent_id not found or not owned by user")
        # `CAST(:pid AS uuid)` handles NULL transparently (CAST(NULL AS uuid)
        # IS NULL). The previous `CASE WHEN :pid IS NULL THEN NULL ELSE
        # CAST(:pid AS uuid) END` form failed on Postgres with asyncpg
        # because the driver couldn't determine the type of the bare
        # untyped NULL branch — "could not determine data type of parameter
        # $2". This is the same root cause that drove the codebase-wide
        # text() CAST fix in PR #92 (see BACKLOG "Tier D").
        result = await db.execute(
            text("""
                INSERT INTO hypotheses
                    (investigation_id, parent_id, statement, rationale, created_by)
                VALUES (CAST(:iid AS uuid),
                        CAST(:pid AS uuid),
                        :stmt, :rationale, :uid)
                RETURNING id::text
            """),
            {
                "iid": investigation_id,
                "pid": parent_id,
                "stmt": statement,
                "rationale": rationale,
                "uid": user_id,
            },
        )
        hyp_id = result.scalar_one()
        await touch_investigation(db, investigation_id)
        return hyp_id


async def list_hypotheses(
    db: AsyncSession,
    investigation_id: str,
    user_id: str,
    status: str | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List hypotheses for an investigation, Elo-desc."""
    if status is not None and status not in _VALID_HYPOTHESIS_STATUSES:
        raise ValueError(
            f"status must be one of {sorted(_VALID_HYPOTHESIS_STATUSES)}, got {status!r}",
        )
    safe_limit = min(max(1, limit), 200)
    params: dict[str, Any] = {"iid": investigation_id, "uid": user_id, "lim": safe_limit}
    status_clause = ""
    if status is not None:
        status_clause = "AND status = :status"
        params["status"] = status
    result = await db.execute(
        text(f"""
            SELECT id::text, parent_id::text, statement, rationale, status,
                   elo_rating, created_at, updated_at
            FROM hypotheses
            WHERE investigation_id = CAST(:iid AS uuid)
              AND created_by = :uid
              {status_clause}
            ORDER BY elo_rating DESC, updated_at DESC, id DESC
            LIMIT :lim
        """),
        params,
    )
    return [dict(r._mapping) for r in result]


async def get_hypothesis(
    db: AsyncSession,
    hypothesis_id: str,
    user_id: str,
) -> dict[str, Any] | None:
    """Single-row fetch, owner-scoped."""
    result = await db.execute(
        text("""
            SELECT id::text, investigation_id::text, parent_id::text,
                   statement, rationale, status, elo_rating,
                   created_at, updated_at
            FROM hypotheses
            WHERE id = CAST(:hid AS uuid)
              AND created_by = :uid
        """),
        {"hid": hypothesis_id, "uid": user_id},
    )
    row = result.one_or_none()
    return dict(row._mapping) if row else None


async def retire_hypothesis(
    db: AsyncSession,
    hypothesis_id: str,
    user_id: str,
) -> bool:
    """Mark a hypothesis retired. Source-state predicate excludes already-
    retired rows so the audit trail is honest about a single transition
    per hypothesis."""
    async with db.begin():
        result = await db.execute(
            text("""
                UPDATE hypotheses
                   SET status = 'retired',
                       updated_at = NOW()
                 WHERE id = CAST(:hid AS uuid)
                   AND created_by = :uid
                   AND status <> 'retired'
            """),
            {"hid": hypothesis_id, "uid": user_id},
        )
        return result.rowcount > 0  # type: ignore[attr-defined]


# ── tournament ranking ───────────────────────────────────────────────────────


async def record_pairwise_ranking(
    db: AsyncSession,
    investigation_id: str,
    user_id: str,
    hypothesis_a_id: str,
    hypothesis_b_id: str,
    winner: str,
    reason: str | None,
    decided_by: str,
    k_factor: float = DEFAULT_K_FACTOR,
) -> dict[str, Any]:
    """Record a pairwise judgment + eager-update both contestants' Elo.

    Both hypotheses must belong to the caller (owner check inside one
    SQL roundtrip). The insert + two updates land in a single
    transaction so ratings and the audit log can't drift.

    Returns:
        {ranking_id, hypothesis_a: {id, new_elo_rating},
         hypothesis_b: {id, new_elo_rating}}
    """
    if winner not in _VALID_WINNERS:
        raise ValueError(f"winner must be one of {sorted(_VALID_WINNERS)}, got {winner!r}")
    if hypothesis_a_id == hypothesis_b_id:
        raise ValueError("a hypothesis cannot be ranked against itself")
    async with db.begin():
        # Fetch both ratings + verify ownership + same investigation in one shot.
        rows = await db.execute(
            text("""
                SELECT id::text, elo_rating, investigation_id::text
                FROM hypotheses
                WHERE id IN (CAST(:a AS uuid), CAST(:b AS uuid))
                  AND created_by = :uid
            """),
            {"a": hypothesis_a_id, "b": hypothesis_b_id, "uid": user_id},
        )
        rating_map: dict[str, dict[str, Any]] = {
            r._mapping["id"]: dict(r._mapping) for r in rows
        }
        if hypothesis_a_id not in rating_map or hypothesis_b_id not in rating_map:
            raise ValueError("one or both hypotheses not found or not owned by user")
        inv_ids = {rating_map[hypothesis_a_id]["investigation_id"],
                   rating_map[hypothesis_b_id]["investigation_id"]}
        if inv_ids != {investigation_id}:
            raise ValueError("hypotheses belong to a different investigation")

        new_a, new_b = elo_update(
            rating_map[hypothesis_a_id]["elo_rating"],
            rating_map[hypothesis_b_id]["elo_rating"],
            winner,
            k=k_factor,
        )

        await db.execute(
            text("""
                UPDATE hypotheses
                   SET elo_rating = :rating,
                       status     = CASE WHEN status = 'proposed' THEN 'ranked' ELSE status END,
                       updated_at = NOW()
                 WHERE id = CAST(:id AS uuid)
                   AND created_by = :uid
            """),
            {"rating": new_a, "id": hypothesis_a_id, "uid": user_id},
        )
        await db.execute(
            text("""
                UPDATE hypotheses
                   SET elo_rating = :rating,
                       status     = CASE WHEN status = 'proposed' THEN 'ranked' ELSE status END,
                       updated_at = NOW()
                 WHERE id = CAST(:id AS uuid)
                   AND created_by = :uid
            """),
            {"rating": new_b, "id": hypothesis_b_id, "uid": user_id},
        )

        insert_result = await db.execute(
            text("""
                INSERT INTO hypothesis_rankings
                    (investigation_id, hypothesis_a_id, hypothesis_b_id,
                     winner, reason, decided_by)
                VALUES (CAST(:iid AS uuid),
                        CAST(:a AS uuid), CAST(:b AS uuid),
                        :winner, :reason, :decided_by)
                RETURNING id::text
            """),
            {
                "iid": investigation_id,
                "a": hypothesis_a_id,
                "b": hypothesis_b_id,
                "winner": winner,
                "reason": reason,
                "decided_by": decided_by,
            },
        )
        ranking_id = insert_result.scalar_one()
        await touch_investigation(db, investigation_id)

    return {
        "ranking_id": ranking_id,
        "hypothesis_a": {"id": hypothesis_a_id, "new_elo_rating": new_a},
        "hypothesis_b": {"id": hypothesis_b_id, "new_elo_rating": new_b},
    }


async def list_recent_rankings(
    db: AsyncSession,
    investigation_id: str,
    user_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Audit-log view. Owner-scoped via the investigation owner check."""
    safe_limit = min(max(1, limit), 200)
    result = await db.execute(
        text("""
            SELECT r.id::text, r.hypothesis_a_id::text, r.hypothesis_b_id::text,
                   r.winner, r.reason, r.decided_by, r.decided_at
            FROM hypothesis_rankings r
            JOIN investigations i ON i.id = r.investigation_id
            WHERE r.investigation_id = CAST(:iid AS uuid)
              AND i.created_by = :uid
            ORDER BY r.decided_at DESC, r.id DESC
            LIMIT :lim
        """),
        {"iid": investigation_id, "uid": user_id, "lim": safe_limit},
    )
    return [dict(r._mapping) for r in result]
