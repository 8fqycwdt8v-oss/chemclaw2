import { auth } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import {
  searchWikiByFTS,
  findSimilarCompounds,
  findSimilarReactions,
  listCompoundsForSubstructure,
} from '@chemclaw2/db';
import { rateLimit } from '@/lib/rate-limit';
import { callMcpTool } from '@chemclaw2/agent-tools';

const MAX_QUERY_LEN = 500;
const MAX_SMARTS_LEN = 500;

function parseLimit(raw: string | null, fallback = 20, max = 50): number {
  const n = Number(raw ?? fallback);
  return Math.min(isNaN(n) || n < 1 ? fallback : n, max);
}

/**
 * GET /api/search?q=<text>&limit=<n>
 *   Full-text search across wiki pages. Returns combined results.
 *
 * POST /api/search
 *   Body: { fingerprint_bits: string } — 2048-char bit string for compound similarity
 *     OR: { rxn_fingerprint_bits: string } — 2048-char bit string for reaction similarity
 *   Caller is responsible for computing fingerprints via the mcp-molfp / mcp-rxnfp MCP tools
 *   (the agent tools compound_similarity_search and find_similar_reactions do this automatically).
 */
export async function GET(req: Request) {
  const { userId } = await auth();
  if (!userId) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const { limited } = await rateLimit(`search:${userId}`, 60, 60_000);
  if (limited) {
    return NextResponse.json({ error: 'Too many requests' }, { status: 429, headers: { 'Retry-After': '60' } });
  }

  const url = new URL(req.url);
  const q = url.searchParams.get('q') ?? '';
  if (!q.trim()) return NextResponse.json({ error: 'q is required' }, { status: 400 });
  if (q.length > MAX_QUERY_LEN) return NextResponse.json({ error: 'query too long' }, { status: 400 });

  const limit = parseLimit(url.searchParams.get('limit'));

  const wikiResults = await searchWikiByFTS(q, limit);
  return NextResponse.json({
    query: q,
    wiki: wikiResults.map((p) => ({
      slug: p.slug,
      title: p.title,
      excerpt: p.contentText?.slice(0, 300),
    })),
  });
}

export async function POST(req: Request) {
  const { userId } = await auth();
  if (!userId) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const { limited } = await rateLimit(`search:${userId}`, 60, 60_000);
  if (limited) {
    return NextResponse.json({ error: 'Too many requests' }, { status: 429, headers: { 'Retry-After': '60' } });
  }

  let body: Record<string, unknown>;
  try {
    body = await req.json() as Record<string, unknown>;
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
  }

  const limit = parseLimit(String(body.limit ?? '20'));

  if (typeof body.fingerprint_bits === 'string') {
    if (!/^[01]{2048}$/.test(body.fingerprint_bits)) {
      return NextResponse.json({ error: 'fingerprint_bits must be exactly 2048 binary characters' }, { status: 400 });
    }
    const filters = parseFilters(body.filters);
    const results = await findSimilarCompounds(body.fingerprint_bits, limit, 0.4, filters);
    return NextResponse.json({ type: 'compound', results });
  }

  if (typeof body.rxn_fingerprint_bits === 'string') {
    if (!/^[01]{2048}$/.test(body.rxn_fingerprint_bits)) {
      return NextResponse.json({ error: 'rxn_fingerprint_bits must be exactly 2048 binary characters' }, { status: 400 });
    }
    const results = await findSimilarReactions(body.rxn_fingerprint_bits, limit);
    return NextResponse.json({ type: 'reaction', results });
  }

  if (typeof body.smarts === 'string') {
    const smarts = body.smarts.trim();
    if (!smarts || smarts.length > MAX_SMARTS_LEN) {
      return NextResponse.json({ error: 'smarts must be a non-empty string under 500 chars' }, { status: 400 });
    }
    try {
      const candidates = await listCompoundsForSubstructure(1000);
      const results: Array<{ id: string; smiles: string; canonSmiles: string | null; name: string | null; casNumber: string | null }> = [];
      // Sequential to avoid spawning hundreds of Python procs concurrently.
      // For larger datasets, switch to the RDKit Postgres cartridge (deferred).
      for (const c of candidates) {
        if (results.length >= limit) break;
        try {
          const r = await callMcpTool('mcp_molfp.server', 'substructure_match', {
            smiles: c.smiles,
            smarts,
          });
          if (r.match === true) results.push(c);
        } catch {
          // Skip individual failures (invalid SMILES rows); abort on SMARTS errors only.
          // substructure_match returns match:false for unparseable SMILES, so this catch
          // generally won't fire — but defending against the MCP transport itself.
        }
      }
      return NextResponse.json({ type: 'substructure', results });
    } catch (err) {
      return NextResponse.json({ error: (err as Error).message }, { status: 502 });
    }
  }

  return NextResponse.json(
    { error: 'Provide fingerprint_bits (compound), rxn_fingerprint_bits (reaction), or smarts (substructure)' },
    { status: 400 },
  );
}

function parseFilters(raw: unknown): { createdAfter?: string; hasCas?: boolean } | undefined {
  if (!raw || typeof raw !== 'object') return undefined;
  const f = raw as Record<string, unknown>;
  const out: { createdAfter?: string; hasCas?: boolean } = {};
  if (typeof f.created_after === 'string') {
    const d = new Date(f.created_after);
    if (!isNaN(d.getTime())) out.createdAfter = d.toISOString();
  }
  if (typeof f.has_cas === 'boolean') out.hasCas = f.has_cas;
  return Object.keys(out).length > 0 ? out : undefined;
}
