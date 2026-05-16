import { lookupKnowledge, type KnowledgeHitType } from '@chemclaw2/db';

type EmbedFn = (text: string) => Promise<number[]>;

type LookupInput = {
  query: string;
  limit?: number;
  types?: KnowledgeHitType[];
  /** Default true. Set false to skip the embedding round-trip when the caller
   *  knows they want lexical-only matches. */
  semantic?: boolean;
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
export function createLookupKnowledgeTool(embedFn: EmbedFn) {
  return {
    name: 'lookup_knowledge',
    description:
      'Search across the organization knowledge graph in one call: wiki ' +
      'pages (FTS + semantic), papers, measured properties (SAR data), and ' +
      'cached results from past web/doc/ELN fetches. Returns a fused, ranked ' +
      'list with each hit labelled by type. Prefer this for "what do we know ' +
      'about X" questions; use the dedicated wiki_lookup / similarity tools ' +
      'when you already know the entity you need.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        query: { type: 'string', description: 'Free-text query' },
        limit: { type: 'integer', minimum: 1, maximum: 50, description: 'Max hits (default 10)' },
        types: {
          type: 'array',
          items: { type: 'string', enum: ['wiki', 'paper', 'property', 'external'] },
          description: 'Subset of sources to consult. Default: all four.',
        },
        semantic: {
          type: 'boolean',
          description: 'Include vector-similarity wiki retrieval. Default true.',
        },
      },
      required: ['query'],
    },
    async execute(input: LookupInput) {
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
