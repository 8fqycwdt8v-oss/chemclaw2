import { safeFetch } from './safe-fetch';

export const ALLOWED_DOMAINS = [
  'pubchem.ncbi.nlm.nih.gov',
  'pubmed.ncbi.nlm.nih.gov',
  'doi.org',
  'crossref.org',
  'chemrxiv.org',
  'rsc.org',
  'acs.org',
  'nature.com',
  'sciencedirect.com',
  'elsevier.com',      // linkinghub.elsevier.com is a common doi.org redirect target
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
    let res: Response;
    try {
      res = await safeFetch(input.url, ALLOWED_DOMAINS, {
        headers: { 'User-Agent': 'chemclaw2/1.0 (research assistant)' },
      });
    } catch (err) {
      return { error: err instanceof Error ? err.message : 'Fetch failed' };
    }
    if (!res.ok) return { error: `HTTP ${res.status}` };
    const contentType = res.headers.get('content-type') ?? '';
    if (!contentType.startsWith('text/')) {
      return { error: `Unsupported content-type: ${contentType.split(';')[0].trim()}` };
    }
    // Stream body with a byte limit to avoid loading large HTML docs into memory
    const MAX_BYTES = 500_000;
    const reader = res.body?.getReader();
    if (!reader) return { error: 'No response body' };
    const chunks: Uint8Array[] = [];
    let totalBytes = 0;
    while (true) {
      const { done, value } = await reader.read();
      if (done || !value) break;
      chunks.push(value);
      totalBytes += value.byteLength;
      if (totalBytes >= MAX_BYTES) { reader.cancel().catch(() => {}); break; }
    }
    const html = new TextDecoder().decode(
      chunks.reduce((acc, c) => { const m = new Uint8Array(acc.length + c.length); m.set(acc); m.set(c, acc.length); return m; }, new Uint8Array()),
    );
    const text = html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 10_000);
    return { url: res.url, text };
  },
};
