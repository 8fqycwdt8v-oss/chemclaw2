import { z } from 'zod';
import { NextResponse } from 'next/server';
import {
  searchWikiByFTS,
  findSimilarCompounds,
  findSimilarReactions,
  listCompoundsForSubstructure,
} from '@chemclaw2/db';
import { callMcpTool } from '@chemclaw2/agent-tools';
import { withRoute, errorResponse } from '@/lib/api-gate';

const MAX_QUERY_LEN = 500;
const MAX_SMARTS_LEN = 500;

function parseLimit(raw: unknown, fallback = 20, max = 50): number {
  const n = Number(raw ?? fallback);
  return Math.min(isNaN(n) || n < 1 ? fallback : n, max);
}

/**
 * GET /api/search?q=<text>&limit=<n>
 *   Full-text search across wiki pages.
 *
 * POST /api/search
 *   { fingerprint_bits } | { rxn_fingerprint_bits } | { smarts } — caller is
 *   responsible for computing fingerprints via the mcp-molfp / mcp-rxnfp MCP
 *   servers (the agent tools do this automatically).
 */
export const GET = withRoute(
  { rateLimit: { key: 'search', max: 60, windowMs: 60_000 } },
  async ({ req }) => {
    const url = new URL(req.url);
    const q = url.searchParams.get('q') ?? '';
    if (!q.trim()) return errorResponse('q is required', 400);
    if (q.length > MAX_QUERY_LEN) return errorResponse('query too long', 400);
    const limit = parseLimit(url.searchParams.get('limit'));
    const wikiResults = await searchWikiByFTS(q, limit);
    return NextResponse.json({
      query: q,
      wiki: wikiResults.map((p) => ({
        slug: p.slug,
        title: p.title,
        excerpt: p.contentText.slice(0, 300),
      })),
    });
  },
);

const FiltersSchema = z
  .object({
    created_after: z.string().optional(),
    has_cas: z.boolean().optional(),
  })
  .partial()
  .optional();

const SearchBody = z
  .object({
    limit: z.union([z.number(), z.string()]).optional(),
    fingerprint_bits: z
      .string()
      .regex(/^[01]{2048}$/, 'fingerprint_bits must be exactly 2048 binary characters')
      .optional(),
    rxn_fingerprint_bits: z
      .string()
      .regex(/^[01]{2048}$/, 'rxn_fingerprint_bits must be exactly 2048 binary characters')
      .optional(),
    smarts: z.string().max(MAX_SMARTS_LEN).optional(),
    filters: FiltersSchema,
  })
  .refine(
    (v) => v.fingerprint_bits || v.rxn_fingerprint_bits || v.smarts,
    {
      message:
        'Provide fingerprint_bits (compound), rxn_fingerprint_bits (reaction), or smarts (substructure)',
    },
  );

export const POST = withRoute(
  { rateLimit: { key: 'search', max: 60, windowMs: 60_000 }, body: SearchBody },
  async ({ body }) => {
    const limit = parseLimit(body.limit);

    if (body.fingerprint_bits) {
      const filters = parseFilters(body.filters);
      const results = await findSimilarCompounds(body.fingerprint_bits, limit, 0.4, filters);
      return NextResponse.json({ type: 'compound', results });
    }
    if (body.rxn_fingerprint_bits) {
      const results = await findSimilarReactions(body.rxn_fingerprint_bits, limit);
      return NextResponse.json({ type: 'reaction', results });
    }
    if (body.smarts) {
      const smarts = body.smarts.trim();
      if (!smarts) return errorResponse('smarts must be a non-empty string under 500 chars', 400);
      try {
        const candidates = await listCompoundsForSubstructure(1000);
        const results: Array<{
          id: string;
          smiles: string;
          canonSmiles: string | null;
          name: string | null;
          casNumber: string | null;
        }> = [];
        // Sequential to avoid spawning hundreds of Python procs concurrently.
        for (const c of candidates) {
          if (results.length >= limit) break;
          try {
            const r = await callMcpTool('mcp_molfp.server', 'substructure_match', {
              smiles: c.smiles,
              smarts,
            });
            if (r.match === true) results.push(c);
          } catch {
            // Skip individual failures; abort on SMARTS errors only.
          }
        }
        return NextResponse.json({ type: 'substructure', results });
      } catch (err) {
        return errorResponse((err as Error).message, 502);
      }
    }

    return errorResponse(
      'Provide fingerprint_bits (compound), rxn_fingerprint_bits (reaction), or smarts (substructure)',
      400,
    );
  },
);

function parseFilters(raw: { created_after?: string; has_cas?: boolean } | undefined):
  | { createdAfter?: string; hasCas?: boolean }
  | undefined {
  if (!raw) return undefined;
  const out: { createdAfter?: string; hasCas?: boolean } = {};
  if (raw.created_after) {
    const d = new Date(raw.created_after);
    if (!isNaN(d.getTime())) out.createdAfter = d.toISOString();
  }
  if (typeof raw.has_cas === 'boolean') out.hasCas = raw.has_cas;
  return Object.keys(out).length > 0 ? out : undefined;
}
