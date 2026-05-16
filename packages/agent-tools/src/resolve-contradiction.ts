import { z } from 'zod';
import {
  db, getWikiPage, wikiCitations, wikiContradictions, wikiChunks, setCitationDisputed,
} from '@chemclaw2/db';
import { eq, and, inArray, sql } from 'drizzle-orm';
import type { ToolDef } from './tool-def';

const readTwoSchema = {
  slug: z.string().describe('Wiki page slug'),
  citation_a: z.string().describe('citation_id of side A'),
  citation_b: z.string().describe('citation_id of side B'),
};

const recordSchema = {
  slug: z.string(),
  citation_a: z.string(),
  citation_b: z.string(),
  winner: z.enum(['a', 'b', 'inconclusive']),
  reason: z.string().describe('Why the winner is more strongly supported (≤1000 chars)'),
};

/**
 * resolve_contradiction: load two citations from a wiki page side-by-side,
 * surface the chunks that mention each marker so the agent can compare
 * supporting context, and record the proposed verdict. record_contradiction
 * also flips wiki_citations.disputed=true on the losing side so the dispute
 * is visible in the UI without a manual reviewer step.
 *
 * Flow inside the agent:
 *   1. read_two_citations(slug, citation_a, citation_b) — returns both citation
 *      rows plus the wiki chunks where each [marker] appears.
 *   2. agent inspects evidence, decides winner + reason.
 *   3. record_contradiction(slug, citation_a, citation_b, winner, reason) —
 *      persists to wiki_contradictions AND marks the loser disputed.
 *
 * Both operations are wrapped here as separate tools.
 */

const MAX_CONTEXT_CHARS = 800;

async function findChunksContainingMarker(pageId: string, marker: string) {
  // [marker] is used in the body text — search wiki_chunks.text for it.
  // Use a parameterized LIKE rather than a regex to keep the index sniff
  // simple; chunks containing `[N]` are the relevant context.
  const needle = `%[${marker}]%`;
  const rows = await db
    .select({ chunkIdx: wikiChunks.chunkIdx, text: wikiChunks.text })
    .from(wikiChunks)
    .where(and(eq(wikiChunks.pageId, pageId), sql`${wikiChunks.text} LIKE ${needle}`))
    .limit(3);
  return rows.map((r) => ({
    chunkIdx: r.chunkIdx,
    text: r.text.length > MAX_CONTEXT_CHARS ? r.text.slice(0, MAX_CONTEXT_CHARS) + '…' : r.text,
  }));
}

export function createContradictionTools(userId: string): {
  readTwo: ToolDef<typeof readTwoSchema>;
  record: ToolDef<typeof recordSchema>;
} {
  const readTwo: ToolDef<typeof readTwoSchema> = {
    name: 'read_two_citations',
    description:
      'Load two citations from a wiki page side-by-side, with the wiki chunks ' +
      'where each [marker] is referenced. Use this before judging which of two ' +
      'disputed claims is better supported — the surrounding chunk text is the ' +
      'evidence the agent must weigh.',
    schema: readTwoSchema,
    async execute(input) {
      const page = await getWikiPage(input.slug);
      if (!page) return { error: 'page not found' };
      const rows = await db
        .select()
        .from(wikiCitations)
        .where(and(
          eq(wikiCitations.pageId, page.id),
          inArray(wikiCitations.citationId, [input.citation_a, input.citation_b]),
        ));
      const a = rows.find((r) => r.citationId === input.citation_a);
      const b = rows.find((r) => r.citationId === input.citation_b);
      if (!a || !b) return { error: 'one or both citations not found on this page' };

      const [contextA, contextB] = await Promise.all([
        findChunksContainingMarker(page.id, a.citationId),
        findChunksContainingMarker(page.id, b.citationId),
      ]);

      return {
        page_id: page.id,
        a: {
          citationId: a.citationId,
          sourceType: a.sourceType,
          sourceId: a.sourceId,
          label: a.label,
          disputed: a.disputed,
          context: contextA,
        },
        b: {
          citationId: b.citationId,
          sourceType: b.sourceType,
          sourceId: b.sourceId,
          label: b.label,
          disputed: b.disputed,
          context: contextB,
        },
      };
    },
  };

  const record: ToolDef<typeof recordSchema> = {
    name: 'record_contradiction',
    description:
      'Persist a proposed resolution between two contradicting citations and ' +
      'flag the losing citation as disputed in the wiki. Call AFTER you have ' +
      'read both citations and decided on a winner. The disputed flag is ' +
      'reversible (a human reviewer can clear it via the dispute UI).',
    schema: recordSchema,
    async execute(input) {
      if (input.reason.length === 0 || input.reason.length > 1000) {
        return { error: 'reason must be 1-1000 chars' };
      }
      const page = await getWikiPage(input.slug);
      if (!page) return { error: 'page not found' };
      const [row] = await db
        .insert(wikiContradictions)
        .values({
          pageId: page.id,
          citationA: input.citation_a,
          citationB: input.citation_b,
          proposedWinner: input.winner,
          reason: input.reason,
          resolvedBy: userId,
        })
        .returning({ id: wikiContradictions.id });

      let disputedMarked: string | null = null;
      if (input.winner === 'a' || input.winner === 'b') {
        const loser = input.winner === 'a' ? input.citation_b : input.citation_a;
        const { found } = await setCitationDisputed(page.id, loser, true);
        if (found) disputedMarked = loser;
      }
      return { id: row.id, winner: input.winner, disputed_citation: disputedMarked };
    },
  };

  return { readTwo, record };
}
