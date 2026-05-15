import { sql } from 'drizzle-orm';
import { db } from '../client';
import { compounds } from '../schema/compounds';

export type SimilarCompound = {
  id: string;
  smiles: string;
  canonSmiles: string | null;
  name: string | null;
  casNumber: string | null;
  tanimoto: number;
};

/**
 * Two-stage similarity search:
 *  1. HNSW ANN pre-filter using Hamming distance (fast, ~100 candidates)
 *  2. Exact Tanimoto re-rank in application code (bit_count(a&b) / bit_count(a|b))
 */
export async function findSimilarCompounds(
  queryFpHex: string,
  limit = 20,
  minTanimoto = 0.4,
): Promise<SimilarCompound[]> {
  const rows = await db
    .select({
      id: compounds.id,
      smiles: compounds.smiles,
      canonSmiles: compounds.canonSmiles,
      name: compounds.name,
      casNumber: compounds.casNumber,
      morganFp: compounds.morganFp,
    })
    .from(compounds)
    .where(sql`morgan_fp IS NOT NULL`)
    .orderBy(sql`morgan_fp <~> ${queryFpHex}::bit(2048)`)
    .limit(100);

  // Exact Tanimoto re-rank: bit_count(a & b) / bit_count(a | b)
  const queryBits = hexToBits(queryFpHex);
  return rows
    .map((row) => ({
      id: row.id,
      smiles: row.smiles,
      canonSmiles: row.canonSmiles,
      name: row.name,
      casNumber: row.casNumber,
      tanimoto: tanimoto(queryBits, hexToBits(row.morganFp!)),
    }))
    .filter((r) => r.tanimoto >= minTanimoto)
    .sort((a, b) => b.tanimoto - a.tanimoto)
    .slice(0, limit);
}

export async function insertCompound(
  smiles: string,
  createdBy: string,
  opts?: { name?: string; casNumber?: string },
): Promise<string> {
  const [row] = await db
    .insert(compounds)
    .values({ smiles, createdBy, name: opts?.name, casNumber: opts?.casNumber })
    .returning({ id: compounds.id });
  return row.id;
}

function hexToBits(hex: string): Uint8Array {
  const bytes = new Uint8Array(hex.length / 2);
  for (let i = 0; i < bytes.length; i++) {
    bytes[i] = parseInt(hex.slice(i * 2, i * 2 + 2), 16);
  }
  return bytes;
}

function tanimoto(a: Uint8Array, b: Uint8Array): number {
  let and = 0;
  let or = 0;
  for (let i = 0; i < a.length; i++) {
    and += bitCount(a[i] & b[i]);
    or += bitCount(a[i] | b[i]);
  }
  return or === 0 ? 0 : and / or;
}

function bitCount(n: number): number {
  n = n - ((n >> 1) & 0x55);
  n = (n & 0x33) + ((n >> 2) & 0x33);
  return ((n + (n >> 4)) & 0x0f) * 0x01010101 >>> 24;
}
