const ALLOWED_DOMAINS = [
  'pubchem.ncbi.nlm.nih.gov',
  'pubmed.ncbi.nlm.nih.gov',
  'doi.org',
  'crossref.org',
  'chemrxiv.org',
  'rsc.org',
  'acs.org',
  'nature.com',
  'sciencedirect.com',
];

export const docFetchTool = {
  name: 'fetch_document',
  description: 'Fetch a scientific document from an allowed domain and return its text content.',
  inputSchema: {
    type: 'object' as const,
    properties: {
      url: { type: 'string', description: 'URL to fetch (must be from an allowed science domain)' },
    },
    required: ['url'],
  },
  async execute(input: { url: string }) {
    let parsed: URL;
    try {
      parsed = new URL(input.url);
    } catch {
      return { error: 'Invalid URL' };
    }
    const hostname = parsed.hostname.replace(/^www\./, '');
    if (!ALLOWED_DOMAINS.some((d) => hostname === d || hostname.endsWith('.' + d))) {
      return { error: `Domain not allowed: ${hostname}. Allowed: ${ALLOWED_DOMAINS.join(', ')}` };
    }
    const res = await fetch(input.url, { headers: { 'User-Agent': 'chemclaw2/1.0 (research assistant)' } });
    if (!res.ok) return { error: `HTTP ${res.status}` };
    const html = await res.text();
    // Strip HTML tags — simple regex
    const text = html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 10_000);
    return { url: input.url, text };
  },
};
