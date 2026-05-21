"""Inline-table loading + cap enforcement for mcp_tabular.

Every tool call ferries data as `columns: list[str]` + `rows: list[list]` —
no pandas/numpy types cross the JSON-RPC boundary. This module is the
single choke-point that converts those primitives into a DataFrame and
enforces size limits.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

# Hard caps. Anything over these is malformed input or a DoS attempt;
# the agent should chunk large data or persist it as an artifact first.
MAX_ROWS = 5_000
MAX_COLS = 256
MAX_CELL_BYTES = 10_000
MAX_OUTPUT_LIST = 10_000


def load_inline(columns: list[str], rows: list[list[Any]]) -> pd.DataFrame:
    """Build a DataFrame from inline columns+rows; enforce caps; coerce dtypes.

    Raises ValueError on cap violations or malformed input.
    """
    if not isinstance(columns, list) or not all(isinstance(c, str) for c in columns):
        raise ValueError("columns must be a list of strings")
    if not isinstance(rows, list):
        raise ValueError("rows must be a list of lists")
    n_cols = len(columns)
    n_rows = len(rows)
    if n_cols == 0:
        raise ValueError("columns must be non-empty")
    if n_cols > MAX_COLS:
        raise ValueError(f"columns exceeds {MAX_COLS} (got {n_cols})")
    if n_rows > MAX_ROWS:
        raise ValueError(f"rows exceeds {MAX_ROWS} (got {n_rows})")
    if len(set(columns)) != n_cols:
        raise ValueError("columns must be unique")
    for i, row in enumerate(rows):
        if not isinstance(row, list):
            raise ValueError(f"row {i} is not a list")
        if len(row) != n_cols:
            raise ValueError(f"row {i} has {len(row)} cells, expected {n_cols}")
        for j, cell in enumerate(row):
            if isinstance(cell, str) and len(cell) > MAX_CELL_BYTES:
                raise ValueError(
                    f"row {i} col {columns[j]!r} exceeds {MAX_CELL_BYTES} bytes"
                )
    df = pd.DataFrame(rows, columns=columns)
    # Best-effort numeric coercion for non-numeric columns. pandas 2.x stores
    # strings as object; pandas 3.x uses a string dtype — handle both.
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]) or pd.api.types.is_bool_dtype(df[col]):
            continue
        coerced = pd.to_numeric(df[col], errors="coerce")
        # Adopt the coercion iff every original non-null value parsed.
        original_non_null = int(df[col].notna().sum())
        if original_non_null > 0 and int(coerced.notna().sum()) == original_non_null:
            df[col] = coerced
    return df


def cap_list(values: list[Any], cap: int = MAX_OUTPUT_LIST) -> list[Any]:
    """Truncate an output list to `cap` items. Callers should report a
    `truncated: bool` flag alongside if they expose the result."""
    return values[:cap]
