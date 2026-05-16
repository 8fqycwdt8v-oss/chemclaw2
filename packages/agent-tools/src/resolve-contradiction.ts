import { db, getWikiPage, wikiCitations, wikiContradictions } from '@chemclaw2/db';
import { eq, and, inArray } from 'drizzle-orm';

/**
 * resolve_contradiction: load two citations from a wiki page, prepare a
 * structured comparison the model can reason over, and record the proposed
 * verdict to wiki_contradictions. The agent calls this tool with the verdict
 * it has chosen — this tool persists, it does NOT itself decide.
 *
 * Flow inside the agent:
 *   1. read_two_citations(slug, citation_a, citation_b) → returns both citation rows.
 *   2. agent inspects evidence, decides winner + reason.
 *   3. record_contradiction(slug, citation_a, citation_b, winner, reason) → persists.
 *
 * Both operations are wrapped here as separate tools.
 */

export function createContradictionTools(userId: string) {
  const readTwo = {
    name: 'read_two_citations',
    description:
      'Load two citations from a wiki page side-by-side. Use this before ' +
      'judging which of two disputed claims is better supported.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        slug: { type: 'string', description: 'Wiki page slug' },
        citation_a: { type: 'string', description: 'citation_id of side A' },
        citation_b: { type: 'string', description: 'citation_id of side B' },
      },
      required: ['slug', 'citation_a', 'citation_b'],
    },
    async execute(input: { slug: string; citation_a: string; citation_b: string }) {
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
      return {
        page_id: page.id,
        a: { citationId: a.citationId, sourceType: a.sourceType, sourceId: a.sourceId, label: a.label, disputed: a.disputed },
        b: { citationId: b.citationId, sourceType: b.sourceType, sourceId: b.sourceId, label: b.label, disputed: b.disputed },
      };
    },
  };

  const record = {
    name: 'record_contradiction',
    description:
      'Persist a proposed resolution between two contradicting citations. ' +
      'Call AFTER you have read both citations and decided on a winner.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        slug: { type: 'string' },
        citation_a: { type: 'string' },
        citation_b: { type: 'string' },
        winner: { type: 'string', enum: ['a', 'b', 'inconclusive'] },
        reason: { type: 'string', description: 'Why the winner is more strongly supported (≤1000 chars)' },
      },
      required: ['slug', 'citation_a', 'citation_b', 'winner', 'reason'],
    },
    async execute(input: {
      slug: string;
      citation_a: string;
      citation_b: string;
      winner: 'a' | 'b' | 'inconclusive';
      reason: string;
    }) {
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
      return { id: row.id, winner: input.winner };
    },
  };

  return { readTwo, record };
}
