/**
 * Convert a Postgres BIT column value (binary string of '0'/'1' chars, 2048 chars long)
 * into a packed Uint8Array for Tanimoto arithmetic.
 */
export function bitStringToBytes(bits: string): Uint8Array {
  const bytes = new Uint8Array(Math.ceil(bits.length / 8));
  for (let i = 0; i < bits.length; i++) {
    if (bits[i] === '1') bytes[i >> 3] |= 1 << (7 - (i & 7));
  }
  return bytes;
}

/** Tanimoto (Jaccard) similarity: bit_count(a & b) / bit_count(a | b) */
export function tanimoto(a: Uint8Array, b: Uint8Array): number {
  let and = 0;
  let or = 0;
  for (let i = 0; i < a.length; i++) {
    and += popcount(a[i] & b[i]);
    or += popcount(a[i] | b[i]);
  }
  return or === 0 ? 0 : and / or;
}

function popcount(n: number): number {
  n = n - ((n >> 1) & 0x55);
  n = (n & 0x33) + ((n >> 2) & 0x33);
  return (((n + (n >> 4)) & 0x0f) * 0x01010101) >>> 24;
}

/**
 * Two-stage similarity over a BIT(2048) HNSW-indexed column.
 * Stage 1: HNSW Hamming pre-filter (~100 candidates via `<~>` operator).
 * Stage 2: exact Tanimoto re-rank in JS, filter ≥ minScore, sort, truncate.
 *
 * Caller provides a select function that returns rows with an `fp` field
 * (the BIT column value as a binary string) plus whatever projection they want.
 */
export function validateFpBits(queryFpBits: string): void {
  if (!/^[01]{2048}$/.test(queryFpBits)) {
    throw new Error('queryFpBits must be exactly 2048 binary characters (0/1)');
  }
}

export function rerankByTanimoto<T extends { fp: string | null }>(
  rows: T[],
  queryFpBits: string,
  minScore: number,
  limit: number,
): Array<T & { similarity: number }> {
  const queryBytes = bitStringToBytes(queryFpBits);
  return rows
    .filter((r): r is T & { fp: string } => r.fp !== null)
    .map((r) => ({ ...r, similarity: tanimoto(queryBytes, bitStringToBytes(r.fp)) }))
    .filter((r) => r.similarity >= minScore)
    .sort((a, b) => b.similarity - a.similarity)
    .slice(0, limit);
}
