import { z } from 'zod';
import { ALLOWED_DOMAINS } from './doc-fetch';
import { recordExternalFactSafe } from '@chemclaw2/db';
import type { ToolDef } from './tool-def';

const BRAVE_API = 'https://api.search.brave.com/res/v1/web/search';

function isAllowedSiteFilter(hostname: string): boolean {
  const h = hostname.toLowerCase();
  return ALLOWED_DOMAINS.some((d) => h === d || h.endsWith('.' + d));
}

const webSearchSchema = {
  query: z.string().describe('Search query'),
  site_filter: z.string().optional().describe(
    'Restrict search to an approved domain (e.g. "pubmed.ncbi.nlm.nih.gov")',
  ),
};

export const webSearchTool: ToolDef<typeof webSearchSchema> = {
  name: 'web_search',
  description:
    'Search the web for scientific literature, patents, or supplier information. ' +
    'site_filter, if provided, must be a hostname from the approved science domain list ' +
    '(pubchem.ncbi.nlm.nih.gov, pubmed.ncbi.nlm.nih.gov, doi.org, crossref.org, ' +
    'chemrxiv.org, rsc.org, acs.org, nature.com, sciencedirect.com, elsevier.com).',
  subagents: ['deep-research'],
  schema: webSearchSchema,
  async execute(input) {
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
    // Raw fetch (not safeFetch) is intentional: BRAVE_API is a hardcoded constant
    // pointing at the Brave Search API, not a user-supplied URL — so the
    // SSRF allowlist that safeFetch enforces is not needed here (#21).
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

/**
 * Wave-2a persistence wrapper. source_id is the normalized search query
 * (site_filter folded in) so repeated identical searches hit the cache.
 * contentText concatenates result titles + snippets for FTS retrievability.
 */
function normalizedSearchKey(query: string, siteFilter?: string): string {
  const q = query.trim().toLowerCase();
  return siteFilter ? `${siteFilter.toLowerCase()}::${q}` : q;
}

export function createWebSearchTool(userId: string): ToolDef<typeof webSearchSchema> {
  return {
    ...webSearchTool,
    async execute(input) {
      const result = await webSearchTool.execute(input) as {
        results: Array<{ title: string; url: string; snippet: string }>;
        error?: string;
      };
      if (Array.isArray(result.results) && result.results.length > 0 && !result.error) {
        const sourceId = normalizedSearchKey(input.query, input.site_filter);
        const contentText = result.results
          .map((r) => `${r.title}\n${r.snippet}`)
          .join('\n\n');
        await recordExternalFactSafe('web_search', sourceId, result, userId, contentText);
      }
      return result;
    },
  };
}
