import { sql } from 'drizzle-orm';
import { db } from '../client';
import { reactions } from '../schema/reactions';
import { rerankByTanimoto, validateFpBits } from './fp-utils';

export type SimilarReaction = {
  id: string;
  rxnSmiles: string;
  name: string | null;
  conditions: string | null;
  similarity: number;
};

export async function findSimilarReactions(
  queryFpBits: string,
  limit = 20,
  minSimilarity = 0.4,
): Promise<SimilarReaction[]> {
  validateFpBits(queryFpBits);
  const safeLimit = Math.max(1, Math.min(limit, 100));
  const safeMin = Math.max(0, Math.min(minSimilarity, 1));
  const rows = await db
    .select({
      id: reactions.id,
      rxnSmiles: reactions.rxnSmiles,
      name: reactions.name,
      conditions: reactions.conditions,
      fp: reactions.drfp,
    })
    .from(reactions)
    .where(sql`drfp IS NOT NULL`)
    .orderBy(sql`drfp <~> ${queryFpBits}::bit(2048)`)
    .limit(100);
  return rerankByTanimoto(rows, queryFpBits, safeMin, safeLimit)
    .map(({ fp: _fp, ...rest }) => rest);
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
