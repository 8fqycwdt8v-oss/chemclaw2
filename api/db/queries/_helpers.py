"""Tiny shared utilities for the queries layer.

Deliberately scoped to mechanical boilerplate that recurred across nearly
every query module: Result→dict mapping, caller-limit clamping, and the
enum-validation error shape. Kept *inside* `api/db/queries/` so the
CLAUDE.md rule "only api/db/queries/* imports SQLAlchemy primitives" still
holds — `rows_to_dicts`/`row_to_dict` touch Result/Row objects.
"""
from __future__ import annotations

from collections.abc import Collection
from typing import Any

from sqlalchemy.engine import Result, Row


def rows_to_dicts(result: Result[Any]) -> list[dict[str, Any]]:
    """Materialise a Result as a list of column→value dicts."""
    return [dict(r._mapping) for r in result]


def row_to_dict(row: Row[Any] | None) -> dict[str, Any] | None:
    """Map a single Row to a dict, or None when the query returned nothing."""
    return dict(row._mapping) if row else None


def clamp_limit(limit: int, max_limit: int) -> int:
    """Clamp a caller-supplied limit into the inclusive range [1, max_limit]."""
    return min(max(1, limit), max_limit)


def validate_enum(value: str, valid: Collection[str], field: str) -> None:
    """Raise ValueError if `value` is not in `valid`.

    Message format ("<field> must be one of [...], got ...") matches the
    convention the rest of the queries layer used and that tests assert on.
    """
    if value not in valid:
        raise ValueError(f"{field} must be one of {sorted(valid)}, got {value!r}")
