"""Fingerprint persistence queries used by `api.workers.fp_worker`.

These functions encapsulate the raw SQL the fingerprint worker needs, so
the worker layer does not import SQLAlchemy primitives directly (CLAUDE.md
code-conventions rule). All updates use `WHERE … fp IS NULL` as a
defense-in-depth idempotency guard — the worker also holds a Postgres
advisory lock per row, so a duplicate compute can only happen if the lock
is lost, in which case the predicate prevents an overwrite.
"""
from __future__ import annotations

from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession


async def fetch_compounds_missing_fp(
    db: AsyncSession, limit: int
) -> list[tuple[str, str]]:
    """Return up to `limit` (id, smiles) pairs of compounds with no Morgan FP."""
    rows = (
        await db.execute(
            text(
                "SELECT id::text AS id, smiles FROM compounds "
                "WHERE morgan_fp IS NULL LIMIT :n"
            ),
            {"n": limit},
        )
    ).fetchall()
    return [(r.id, r.smiles) for r in rows]


async def fetch_reactions_missing_fp(
    db: AsyncSession, limit: int
) -> list[tuple[str, str]]:
    """Return up to `limit` (id, rxn_smiles) pairs of reactions with no DRFP."""
    rows = (
        await db.execute(
            text(
                "SELECT id::text AS id, rxn_smiles FROM reactions "
                "WHERE drfp IS NULL LIMIT :n"
            ),
            {"n": limit},
        )
    ).fetchall()
    return [(r.id, r.rxn_smiles) for r in rows]


async def try_acquire_fp_lock(db: AsyncSession, key: int) -> bool:
    """Acquire a Postgres advisory lock for the given key. Non-blocking."""
    result = await db.execute(
        text("SELECT pg_try_advisory_lock(:k)"), {"k": key}
    )
    return bool(result.scalar())


async def release_fp_lock(db: AsyncSession, key: int) -> None:
    await db.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})


async def write_compound_fp(
    db: AsyncSession, compound_id: str, bits: str, popcount: int
) -> None:
    """Idempotent write: predicate `morgan_fp IS NULL` prevents overwrite."""
    await db.execute(
        text(
            """
            UPDATE compounds
            SET morgan_fp = CAST(:bits AS bit(2048)),
                morgan_fp_popcount = :pc,
                fp_computed_at = now()
            WHERE id = CAST(:id AS uuid) AND morgan_fp IS NULL
            """
        ),
        {"bits": bits, "pc": popcount, "id": compound_id},
    )


async def write_reaction_fp(
    db: AsyncSession, reaction_id: str, bits: str
) -> None:
    """Idempotent write: predicate `drfp IS NULL` prevents overwrite."""
    await db.execute(
        text(
            """
            UPDATE reactions
            SET drfp = CAST(:bits AS bit(2048)),
                fp_computed_at = now()
            WHERE id = CAST(:id AS uuid) AND drfp IS NULL
            """
        ),
        {"bits": bits, "id": reaction_id},
    )
