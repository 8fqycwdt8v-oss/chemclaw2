"""Shared request-validation helpers for the route layer.

Keyset-cursor parsing and canonical-UUID checks were copy-pasted across the
wiki and campaigns routers (the cursor block verbatim in two places, the
UUID regex in four call sites). Centralise them so the format lives in one
place and a future change to the cursor encoding touches a single function.
"""
from __future__ import annotations

import re
from datetime import datetime

from fastapi import HTTPException

UUID_RE = re.compile(
    r'^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$', re.I
)


def is_uuid(value: str) -> bool:
    """True if `value` is a canonical 8-4-4-4-12 hex UUID."""
    return bool(UUID_RE.match(value))


def parse_keyset_cursor(cursor: str) -> tuple[datetime, str]:
    """Parse an opaque `<updated_at_isoformat>_<uuid>` keyset cursor.

    Raises ``HTTPException(400, "Invalid cursor")`` on any malformed
    component — missing separator, unparseable timestamp, or non-UUID id —
    so every paginated list endpoint rejects a bad cursor identically.
    """
    sep = cursor.rfind('_')
    if sep == -1:
        raise HTTPException(status_code=400, detail="Invalid cursor")
    try:
        updated_at = datetime.fromisoformat(cursor[:sep])
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid cursor") from None
    cursor_id = cursor[sep + 1:]
    if not is_uuid(cursor_id):
        raise HTTPException(status_code=400, detail="Invalid cursor")
    return updated_at, cursor_id
