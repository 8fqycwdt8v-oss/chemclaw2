"""Argument validators shared across the in-process MCP tool bodies.

These dedupe the two argument checks that recurred verbatim across the
`tools_*.py` builders (CLAUDE.md "extract on the third copy"): the
2048-bit fingerprint match and the UUID parse-or-reject. Only the
matching/parsing *logic* lives here — each call site keeps its own error
message and result-dict shape (some tools answer `{"error": ...}`, others
`{"ok": False, "error": ...}`, and a couple treat the value as optional),
so both helpers return a plain bool/Optional the caller branches on.
"""
from __future__ import annotations

import re
import uuid

_FINGERPRINT_RE = re.compile(r"^[01]{2048}$")


def is_fingerprint(bits: str) -> bool:
    """True if `bits` is exactly 2048 binary digits (a Morgan/DRFP bitstring)."""
    return bool(_FINGERPRINT_RE.match(bits))


def parse_uuid(value: str) -> str | None:
    """Return the canonical UUID string for `value`, or None if it isn't one.

    Returning `None` (rather than the error text) lets the caller emit its
    own message + result-dict shape and lets the type checker narrow the
    result to `str` after an `if ... is None:` guard.
    """
    try:
        return str(uuid.UUID(value.strip()))
    except (ValueError, AttributeError):
        return None
