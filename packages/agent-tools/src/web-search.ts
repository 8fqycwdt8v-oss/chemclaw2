const BRAVE_API = 'https://api.search.brave.com/res/v1/web/search';

export const webSearchTool = {
  name: 'web_search',
  description: 'Search the web for scientific literature, patents, or supplier information.',
  inputSchema: {
    type: 'object' as const,
    properties: {
      query: { type: 'string', description: 'Search query' },
      site_filter: { type: 'string', description: 'Optional site: filter (e.g. "pubmed.ncbi.nlm.nih.gov")' },
    },
    required: ['query'],
  },
  async execute(input: { query: string; site_filter?: string }) {
    const apiKey = process.env.BRAVE_SEARCH_API_KEY;
    if (!apiKey) {
      return { results: [], error: 'BRAVE_SEARCH_API_KEY not configured' };
    }
    if (input.site_filter && !/^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/.test(input.site_filter)) {
      return { results: [], error: 'Invalid site_filter: must be a bare hostname (e.g. pubmed.ncbi.nlm.nih.gov)' };
    }
    const q = input.site_filter ? `site:${input.site_filter} ${input.query}` : input.query;
    const url = `${BRAVE_API}?q=${encodeURIComponent(q)}&count=5`;
    const res = await fetch(url, { headers: { 'X-Subscription-Token': apiKey, Accept: 'application/json' } });
    if (!res.ok) return { results: [], error: `Brave API error: ${res.status}` };
    const data = await res.json() as { web?: { results?: Array<{ title: string; url: string; description: string }> } };
    const results = (data.web?.results ?? []).map((r) => ({
      title: r.title,
      url: r.url,
      snippet: r.description,
    }));
    return { results };
  },
};
