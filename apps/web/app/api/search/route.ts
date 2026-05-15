import { auth } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { searchWikiByFTS, findSimilarCompounds, findSimilarReactions } from '@chemclaw2/db';
import { rateLimit } from '@/lib/rate-limit';

const MAX_QUERY_LEN = 500;

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

  const url = new URL(req.url);
  const q = url.searchParams.get('q') ?? '';
  if (!q.trim()) return NextResponse.json({ error: 'q is required' }, { status: 400 });
  if (q.length > MAX_QUERY_LEN) return NextResponse.json({ error: 'query too long' }, { status: 400 });

  const limit = Math.min(Number(url.searchParams.get('limit') ?? '20'), 50);

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

  const { limited } = rateLimit(`search:${userId}`, 60, 60_000);
  if (limited) {
    return NextResponse.json({ error: 'Too many requests' }, { status: 429, headers: { 'Retry-After': '60' } });
  }

  let body: Record<string, unknown>;
  try {
    body = await req.json() as Record<string, unknown>;
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
  }

  const limit = Math.min(Number(body.limit ?? 20), 50);

  if (typeof body.fingerprint_bits === 'string') {
    if (!/^[01]{2048}$/.test(body.fingerprint_bits)) {
      return NextResponse.json({ error: 'fingerprint_bits must be exactly 2048 binary characters' }, { status: 400 });
    }
    const results = await findSimilarCompounds(body.fingerprint_bits, limit);
    return NextResponse.json({ type: 'compound', results });
  }

  if (typeof body.rxn_fingerprint_bits === 'string') {
    if (!/^[01]{2048}$/.test(body.rxn_fingerprint_bits)) {
      return NextResponse.json({ error: 'rxn_fingerprint_bits must be exactly 2048 binary characters' }, { status: 400 });
    }
    const results = await findSimilarReactions(body.rxn_fingerprint_bits, limit);
    return NextResponse.json({ type: 'reaction', results });
  }

  return NextResponse.json(
    { error: 'Provide fingerprint_bits (compound) or rxn_fingerprint_bits (reaction)' },
    { status: 400 },
  );
}
