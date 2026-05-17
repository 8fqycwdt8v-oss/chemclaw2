"""Fingerprint utilities — Python port of packages/db/src/queries/fp-utils.ts."""
from __future__ import annotations

import re


_FP_RE = re.compile(r'^[01]{2048}$')


def validate_fp_bits(bits: str) -> None:
    if not _FP_RE.match(bits):
        raise ValueError("fp_bits must be exactly 2048 binary characters (0/1)")


def bit_string_to_bytes(bits: str) -> bytearray:
    n = len(bits)
    out = bytearray((n + 7) // 8)
    for i, ch in enumerate(bits):
        if ch == '1':
            out[i >> 3] |= 1 << (7 - (i & 7))
    return out


def tanimoto(a: bytearray, b: bytearray) -> float:
    """Tanimoto (Jaccard) similarity over packed bit arrays."""
    and_count = 0
    or_count = 0
    for x, y in zip(a, b):
        and_count += bin(x & y).count('1')
        or_count += bin(x | y).count('1')
    return and_count / or_count if or_count else 0.0


def rerank_by_tanimoto(
    rows: list[dict],
    query_fp_bits: str,
    min_score: float,
    limit: int,
) -> list[dict]:
    """Stage-2 exact Tanimoto re-rank over HNSW pre-filtered candidates."""
    query_bytes = bit_string_to_bytes(query_fp_bits)
    results = []
    for row in rows:
        fp = row.get("fp")
        if not fp:
            continue
        sim = tanimoto(query_bytes, bit_string_to_bytes(fp))
        if sim >= min_score:
            results.append({**row, "similarity": sim})
    results.sort(key=lambda r: r["similarity"], reverse=True)
    return results[:limit]
