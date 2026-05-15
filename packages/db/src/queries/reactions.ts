import { sql } from 'drizzle-orm';
import { db } from '../client';
import { reactions } from '../schema/reactions';
import { bitStringToBytes, tanimoto } from './fp-utils';

export type SimilarReaction = {
  id: string;
  rxnSmiles: string;
  name: string | null;
  conditions: string | null;
  similarity: number;
};

/**
 * queryFpBits: binary string of '0'/'1' chars (2048 chars), as returned by
 * the mcp-rxnfp compute_drfp tool's `fingerprint_bits` field.
 */
export async function findSimilarReactions(
  queryFpBits: string,
  limit = 20,
  minSimilarity = 0.4,
): Promise<SimilarReaction[]> {
  if (!/^[01]{2048}$/.test(queryFpBits)) {
    throw new Error('queryFpBits must be exactly 2048 binary characters (0/1)');
  }
  const rows = await db
    .select({
      id: reactions.id,
      rxnSmiles: reactions.rxnSmiles,
      name: reactions.name,
      conditions: reactions.conditions,
      drfp: reactions.drfp,
    })
    .from(reactions)
    .where(sql`drfp IS NOT NULL`)
    .orderBy(sql`drfp <~> ${queryFpBits}::bit(2048)`)
    .limit(100);

  const queryBytes = bitStringToBytes(queryFpBits);
  return rows
    .map((row) => ({
      id: row.id,
      rxnSmiles: row.rxnSmiles,
      name: row.name,
      conditions: row.conditions,
      similarity: tanimoto(queryBytes, bitStringToBytes(row.drfp!)),
    }))
    .filter((r) => r.similarity >= minSimilarity)
    .sort((a, b) => b.similarity - a.similarity)
    .slice(0, limit);
}

export async function insertReaction(
  rxnSmiles: string,
  createdBy: string,
  opts?: { name?: string; conditions?: string },
): Promise<string> {
  const [row] = await db
    .insert(reactions)
    .values({ rxnSmiles, createdBy, name: opts?.name, conditions: opts?.conditions })
    .returning({ id: reactions.id });
  return row.id;
}
