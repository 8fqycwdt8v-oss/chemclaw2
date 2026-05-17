import { sql, eq, and, inArray } from 'drizzle-orm';
import { db } from '../client';
import { wikiCitations, wikiChunks, wikiContradictions } from '../schema/wiki';

/**
 * Citations recorded against a page, with `disputed` flag for the UI.
 */
export async function getWikiPageCitations(pageId: string) {
  return db
    .select({
      citationId: wikiCitations.citationId,
      sourceType: wikiCitations.sourceType,
      sourceId: wikiCitations.sourceId,
      label: wikiCitations.label,
      disputed: wikiCitations.disputed,
    })
    .from(wikiCitations)
    .where(eq(wikiCitations.pageId, pageId));
}

/**
 * Mark a single citation as disputed (or undisputed). The citation row remains —
 * audit + reader trace are preserved; UI strikes it through.
 */
export async function setCitationDisputed(
  pageId: string,
  citationId: string,
  disputed: boolean,
): Promise<{ found: boolean }> {
  const rows = await db
    .update(wikiCitations)
    .set({ disputed })
    .where(and(eq(wikiCitations.pageId, pageId), eq(wikiCitations.citationId, citationId)))
    .returning({ id: wikiCitations.id });
  return { found: rows.length > 0 };
}

/**
 * Fetch two citation rows on the same page by their citationId values. Used
 * by the contradiction-resolver flow to load both sides for evidence-comparison.
 */
export async function getCitationPair(
  pageId: string,
  citationA: string,
  citationB: string,
): Promise<Array<typeof wikiCitations.$inferSelect>> {
  return db
    .select()
    .from(wikiCitations)
    .where(and(
      eq(wikiCitations.pageId, pageId),
      inArray(wikiCitations.citationId, [citationA, citationB]),
    ));
}

/** LIKE-pattern metacharacter escape. */
function escapeLikePattern(s: string): string {
  return s.replace(/[\\%_]/g, (c) => `\\${c}`);
}

/**
 * Up to `limit` chunks on the page where the literal `[marker]` text occurs,
 * for surfacing citation context to the contradiction-resolver agent.
 */
export async function findChunksContainingCitationMarker(
  pageId: string,
  marker: string,
  opts: { limit?: number; maxContextChars?: number } = {},
): Promise<Array<{ chunkIdx: number; text: string }>> {
  const limit = opts.limit ?? 3;
  const maxContextChars = opts.maxContextChars ?? 800;
  const needle = `%[${escapeLikePattern(marker)}]%`;
  const rows = await db
    .select({ chunkIdx: wikiChunks.chunkIdx, text: wikiChunks.text })
    .from(wikiChunks)
    .where(and(
      eq(wikiChunks.pageId, pageId),
      sql`${wikiChunks.text} LIKE ${needle} ESCAPE '\\'`,
    ))
    .orderBy(wikiChunks.chunkIdx)
    .limit(limit);
  return rows.map((r) => ({
    chunkIdx: r.chunkIdx,
    text: r.text.length > maxContextChars ? r.text.slice(0, maxContextChars) + '…' : r.text,
  }));
}

/** Insert a proposed contradiction resolution. Returns the new row id. */
export async function recordContradiction(input: {
  pageId: string;
  citationA: string;
  citationB: string;
  proposedWinner: 'a' | 'b' | 'inconclusive';
  reason: string;
  resolvedBy: string;
}): Promise<{ id: string }> {
  const [row] = await db
    .insert(wikiContradictions)
    .values({
      pageId: input.pageId,
      citationA: input.citationA,
      citationB: input.citationB,
      proposedWinner: input.proposedWinner,
      reason: input.reason,
      resolvedBy: input.resolvedBy,
    })
    .returning({ id: wikiContradictions.id });
  return { id: row.id };
}
