import { sql } from 'drizzle-orm';
import { db } from '../client';
import { reactions } from '../schema/reactions';

export type SimilarReaction = {
  id: string;
  rxnSmiles: string;
  name: string | null;
  conditions: string | null;
  similarity: number;
};

export async function findSimilarReactions(
  queryFpHex: string,
  limit = 20,
  minSimilarity = 0.4,
): Promise<SimilarReaction[]> {
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
    .orderBy(sql`drfp <~> ${queryFpHex}::bit(2048)`)
    .limit(100);

  const queryBits = hexToBits(queryFpHex);
  return rows
    .map((row) => ({
      id: row.id,
      rxnSmiles: row.rxnSmiles,
      name: row.name,
      conditions: row.conditions,
      similarity: tanimoto(queryBits, hexToBits(row.drfp!)),
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
