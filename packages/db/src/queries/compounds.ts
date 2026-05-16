import { sql } from 'drizzle-orm';
import { db } from '../client';
import { compounds } from '../schema/compounds';
import { bitStringToBytes, tanimoto } from './fp-utils';

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
 *
 * queryFpBits: binary string of '0'/'1' chars (2048 chars), as returned by
 * the mcp-molfp compute_morgan_fp tool's `fingerprint_bits` field.
 */
export async function findSimilarCompounds(
  queryFpBits: string,
  limit = 20,
  minTanimoto = 0.4,
): Promise<SimilarCompound[]> {
  const safeLimit = Math.min(limit, 100);
  if (!/^[01]{2048}$/.test(queryFpBits)) {
    throw new Error('queryFpBits must be exactly 2048 binary characters (0/1)');
  }
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
    // Postgres accepts a binary string ('010101...') cast to bit(2048)
    .orderBy(sql`morgan_fp <~> ${queryFpBits}::bit(2048)`)
    .limit(100);

  const queryBytes = bitStringToBytes(queryFpBits);
  return rows
    .map((row) => ({
      id: row.id,
      smiles: row.smiles,
      canonSmiles: row.canonSmiles,
      name: row.name,
      casNumber: row.casNumber,
      // Postgres returns BIT columns as binary strings ('010101...')
      tanimoto: tanimoto(queryBytes, bitStringToBytes(row.morganFp!)),
    }))
    .filter((r) => r.tanimoto >= minTanimoto)
    .sort((a, b) => b.tanimoto - a.tanimoto)
    .slice(0, safeLimit);
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
