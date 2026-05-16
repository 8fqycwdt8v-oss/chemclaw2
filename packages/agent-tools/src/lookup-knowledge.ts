import { z } from 'zod';
import { lookupKnowledge } from '@chemclaw2/db';
import type { ToolDef } from './tool-def';

type EmbedFn = (text: string) => Promise<number[]>;

const lookupKnowledgeSchema = {
  query: z.string().describe('Free-text query'),
  limit: z.number().int().min(1).max(50).optional().describe('Max hits (default 10)'),
  types: z.array(z.enum(['wiki', 'paper', 'property', 'external'])).optional().describe(
    'Subset of sources to consult. Default: all four.',
  ),
  semantic: z.boolean().optional().describe(
    'Include vector-similarity wiki retrieval. Default true.',
  ),
};

/**
 * Wave-2b C3 agent tool: one retrieval call across wiki + papers + properties
 * + external_facts (cached prior tool fetches). Results are fused via RRF and
 * returned as a typed mix the agent can triage.
 *
 * Designed to absorb the everyday "what do we know about X" lookups so the
 * agent stops sequencing wiki_lookup → docs → prior fetches manually. The
 * granular tools (wiki_lookup with full=true, compound_similarity_search,
 * find_similar_reactions) stay available for targeted reads.
 */
export function createLookupKnowledgeTool(embedFn: EmbedFn): ToolDef<typeof lookupKnowledgeSchema> {
  return {
    name: 'lookup_knowledge',
    description:
      'Search across the organization knowledge graph in one call: wiki ' +
      'pages (FTS + semantic), papers, measured properties (SAR data), and ' +
      'cached results from past web/doc/ELN fetches. Returns a fused, ranked ' +
      'list with each hit labelled by type. Prefer this for "what do we know ' +
      'about X" questions; use the dedicated wiki_lookup / similarity tools ' +
      'when you already know the entity you need.',
    schema: lookupKnowledgeSchema,
    async execute(input) {
      const q = input.query.trim();
      if (q.length === 0 || q.length > 500) {
        return { error: 'query must be 1-500 chars after trimming' };
      }
      const semantic = input.semantic !== false; // default true
      const hits = await lookupKnowledge(q, {
        limit: input.limit,
        types: input.types,
        embedFn: semantic ? embedFn : undefined,
      });
      return {
        query: q,
        count: hits.length,
        hits: hits.map((h) => ({
          type: h.type,
          id: h.id,
          title: h.title,
          excerpt: h.excerpt,
          metadata: h.metadata,
        })),
      };
    },
  };
}
