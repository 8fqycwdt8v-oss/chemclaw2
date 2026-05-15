import { getWikiPage, searchWikiByFTS, semanticSearchWiki } from '@chemclaw2/db';

const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

type EmbedFn = (text: string) => Promise<number[]>;

async function executeWikiLookup(
  input: { slug?: string; query?: string; semantic?: boolean },
  embedFn?: EmbedFn,
) {
  if (input.slug) {
    if (!SLUG_RE.test(input.slug) || input.slug.length > 200) {
      return { error: 'Invalid slug format' };
    }
    const page = await getWikiPage(input.slug);
    if (!page) return { found: false };
    return {
      found: true,
      title: page.title,
      text: page.contentText?.slice(0, 2000) ?? '',
      slug: page.slug,
      version: page.version,
    };
  }
  if (input.query) {
    if (input.query.length > 500) return { error: 'Query too long (max 500 chars)' };
    if (input.semantic && embedFn) {
      const embedding = await embedFn(input.query);
      const results = await semanticSearchWiki(embedding, 5);
      return { mode: 'semantic', results };
    }
    const results = await searchWikiByFTS(input.query, 5);
    return {
      mode: 'fts',
      results: results.map((r) => ({ slug: r.slug, title: r.title, excerpt: r.contentText?.slice(0, 300) })),
    };
  }
  return { error: 'Provide either slug or query' };
}

/** Plain execute-only version (no SDK dependency) */
export const wikiFetchTool = {
  name: 'wiki_lookup',
  description: 'Look up or search the organization wiki. Provide slug for direct lookup, query for full-text search, or query+semantic=true for vector similarity search.',
  inputSchema: {
    type: 'object' as const,
    properties: {
      slug: { type: 'string', description: 'Direct page slug (e.g. "aspirin")' },
      query: { type: 'string', description: 'Full-text or semantic search query' },
      semantic: { type: 'boolean', description: 'Use vector similarity search (requires query)' },
    },
  },
  execute: (input: { slug?: string; query?: string; semantic?: boolean }) =>
    executeWikiLookup(input),
};

/** Factory that returns a wiki tool wired with an embed function for semantic search. */
export function createWikiFetchTool(embedFn: EmbedFn) {
  return {
    ...wikiFetchTool,
    execute: (input: { slug?: string; query?: string; semantic?: boolean }) =>
      executeWikiLookup(input, embedFn),
  };
}
