import { getWikiPage, searchWikiByFTS } from '@chemclaw2/db';

export const wikiFetchTool = {
  name: 'wiki_lookup',
  description: 'Look up or search the organization wiki. Provide slug for direct lookup, or query for full-text search.',
  inputSchema: {
    type: 'object' as const,
    properties: {
      slug: { type: 'string', description: 'Direct page slug (e.g. "aspirin")' },
      query: { type: 'string', description: 'Full-text search query' },
    },
  },
  async execute(input: { slug?: string; query?: string }) {
    if (input.slug) {
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
      const results = await searchWikiByFTS(input.query, 5);
      return { results: results.map((r) => ({ slug: r.slug, title: r.title, excerpt: r.contentText?.slice(0, 300) })) };
    }
    return { error: 'Provide either slug or query' };
  },
};
