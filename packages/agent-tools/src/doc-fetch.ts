import { z } from 'zod';
import { safeFetch } from './safe-fetch';
import { recordExternalFactSafe } from '@chemclaw2/db';
import { toolError } from './tool-error';
import type { ToolDef } from './tool-def';

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

const docFetchSchema = {
  url: z.string().url().describe('URL to fetch (must be from an allowed science domain)'),
  format: z.enum(['markdown', 'html', 'bytes']).optional().describe(
    'Output format (default: markdown)',
  ),
};

export const docFetchTool: ToolDef<typeof docFetchSchema> = {
  name: 'fetch_document',
  description:
    'Fetch a scientific document from an allowed domain. format=markdown (default) ' +
    'returns stripped text, format=html returns the raw HTML (size-capped), ' +
    'format=bytes returns base64 + content-type for non-text downloads like PDFs.',
  subagents: ['deep-research', 'contradiction-resolver'],
  schema: docFetchSchema,
  async execute(input) {
    const format = input.format ?? 'markdown';
    let res: Response;
    try {
      res = await safeFetch(input.url, ALLOWED_DOMAINS, {
        headers: { 'User-Agent': 'chemclaw2/1.0 (research assistant)' },
      });
    } catch (err) {
      return toolError('fetch_document', err);
    }
    if (!res.ok) return { error: `HTTP ${res.status}` };
    const contentType = res.headers.get('content-type') ?? '';
    const MAX_BYTES = 500_000;

    // For markdown/html, require text content-type. bytes mode accepts anything
    // (including application/pdf) up to the same byte cap.
    if (format !== 'bytes' && !contentType.startsWith('text/')) {
      return { error: `Unsupported content-type for ${format}: ${contentType.split(';')[0].trim()}` };
    }

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
    const bytes = Buffer.concat(chunks);

    if (format === 'bytes') {
      return { url: res.url, content_type: contentType, bytes_b64: Buffer.from(bytes).toString('base64') };
    }
    const html = new TextDecoder().decode(bytes);
    if (format === 'html') {
      return { url: res.url, html: html.slice(0, 50_000) };
    }
    const text = html.replace(/<[^>]+>/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 10_000);
    return { url: res.url, text };
  },
};

/**
 * Wave-2a persistence wrapper. source_id is the canonical URL post-redirect
 * (res.url) so two paths that resolve to the same canonical page share the
 * cache. contentText is the plain-text extract (markdown mode); for html/
 * bytes mode we store payload only — those formats aren't FTS-friendly.
 */
export function createDocFetchTool(userId: string): ToolDef<typeof docFetchSchema> {
  return {
    ...docFetchTool,
    async execute(input) {
      const result = await docFetchTool.execute(input);
      if (typeof result === 'object' && result && 'url' in result && !('error' in result)) {
        const canonicalUrl = (result as { url: string }).url;
        const contentText = 'text' in result ? (result as { text: string }).text : null;
        await recordExternalFactSafe('doc', canonicalUrl, result, userId, contentText);
      }
      return result;
    },
  };
}
