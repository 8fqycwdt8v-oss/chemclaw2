import { ALLOWED_DOMAINS } from './doc-fetch';

const BRAVE_API = 'https://api.search.brave.com/res/v1/web/search';

function isAllowedSiteFilter(hostname: string): boolean {
  const h = hostname.toLowerCase();
  return ALLOWED_DOMAINS.some((d) => h === d || h.endsWith('.' + d));
}

export const webSearchTool = {
  name: 'web_search',
  description:
    'Search the web for scientific literature, patents, or supplier information. ' +
    'site_filter, if provided, must be a hostname from the approved science domain list ' +
    '(pubchem.ncbi.nlm.nih.gov, pubmed.ncbi.nlm.nih.gov, doi.org, crossref.org, ' +
    'chemrxiv.org, rsc.org, acs.org, nature.com, sciencedirect.com, elsevier.com).',
  inputSchema: {
    type: 'object' as const,
    properties: {
      query: { type: 'string', description: 'Search query' },
      site_filter: { type: 'string', description: 'Restrict search to an approved domain (e.g. "pubmed.ncbi.nlm.nih.gov")' },
    },
    required: ['query'],
  },
  async execute(input: { query: string; site_filter?: string }) {
    const q = input.query.trim();
    if (q.length === 0 || q.length > 500) {
      return { results: [], error: 'query must be 1-500 chars after trimming' };
    }
    const apiKey = process.env.BRAVE_SEARCH_API_KEY;
    if (!apiKey) {
      return { results: [], error: 'BRAVE_SEARCH_API_KEY not configured' };
    }
    if (input.site_filter) {
      if (!/^[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$/.test(input.site_filter)) {
        return { results: [], error: 'Invalid site_filter: must be a bare hostname (e.g. pubmed.ncbi.nlm.nih.gov)' };
      }
      if (!isAllowedSiteFilter(input.site_filter.toLowerCase())) {
        return { results: [], error: `site_filter '${input.site_filter}' is not in the approved domain list` };
      }
    }
    const normalizedFilter = input.site_filter?.toLowerCase();
    const finalQ = normalizedFilter ? `site:${normalizedFilter} ${q}` : q;
    const url = `${BRAVE_API}?q=${encodeURIComponent(finalQ)}&count=5`;
    const res = await fetch(url, { headers: { 'X-Subscription-Token': apiKey, Accept: 'application/json' } });
    if (!res.ok) return { results: [], error: `Brave API error: ${res.status}` };
    const MAX_BYTES = 500_000;
    const raw = await res.text();
    if (Buffer.byteLength(raw, 'utf8') > MAX_BYTES) {
      return { results: [], error: 'Brave API response exceeds size limit' };
    }
    let data: { web?: { results?: Array<{ title: string; url: string; description: string }> } };
    try {
      data = JSON.parse(raw) as typeof data;
    } catch {
      return { results: [], error: 'Brave API returned non-JSON response' };
    }
    const results = (data.web?.results ?? []).map((r) => ({
      title: r.title,
      url: r.url,
      snippet: r.description,
    }));
    return { results };
  },
};
