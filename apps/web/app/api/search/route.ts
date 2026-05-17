import { auth } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import {
  searchWikiByFTS,
  findSimilarCompounds,
  findSimilarReactions,
  listCompoundsForSubstructure,
} from '@chemclaw2/db';
import { rateLimit } from '@/lib/rate-limit';
import { withApiContext } from '@/lib/api-context';
import { callMcpTool } from '@chemclaw2/agent-tools';
import { logger } from '@chemclaw2/observability';

const MAX_QUERY_LEN = 500;
const MAX_SMARTS_LEN = 500;

function parseLimit(raw: string | null, fallback = 20, max = 50): number {
  const n = Number(raw ?? fallback);
  return Math.min(isNaN(n) || n < 1 ? fallback : n, max);
}

export async function GET(req: Request) {
  return withApiContext(async () => {
    const { userId } = await auth();
    if (!userId) {
      logger.info('auth_denied', { route: 'search', method: 'GET' });
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const { limited } = await rateLimit(`search:${userId}`, 60, 60_000);
    if (limited) {
      logger.warn('rate_limit_hit', { route: 'search', method: 'GET', user_id: userId });
      return NextResponse.json({ error: 'Too many requests' }, { status: 429, headers: { 'Retry-After': '60' } });
    }

    const url = new URL(req.url);
    const q = url.searchParams.get('q') ?? '';
    if (!q.trim()) {
      logger.info('validation_rejected', { route: 'search', field: 'q', reason: 'empty' });
      return NextResponse.json({ error: 'q is required' }, { status: 400 });
    }
    if (q.length > MAX_QUERY_LEN) {
      logger.info('validation_rejected', { route: 'search', field: 'q', reason: 'oversize', length: q.length });
      return NextResponse.json({ error: 'query too long' }, { status: 400 });
    }

    const limit = parseLimit(url.searchParams.get('limit'));

    const wikiResults = await searchWikiByFTS(q, limit).catch((err) => {
      logger.error('search_wiki_fts_failed', { route: 'search', q_len: q.length }, err);
      throw err;
    });
    return NextResponse.json({
      query: q,
      wiki: wikiResults.map((p) => ({
        slug: p.slug,
        title: p.title,
        excerpt: p.contentText.slice(0, 300),
      })),
    });
  });
}

export async function POST(req: Request) {
  return withApiContext(async () => {
    const { userId } = await auth();
    if (!userId) {
      logger.info('auth_denied', { route: 'search', method: 'POST' });
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const { limited } = await rateLimit(`search:${userId}`, 60, 60_000);
    if (limited) {
      logger.warn('rate_limit_hit', { route: 'search', method: 'POST', user_id: userId });
      return NextResponse.json({ error: 'Too many requests' }, { status: 429, headers: { 'Retry-After': '60' } });
    }

    let body: Record<string, unknown>;
    try {
      body = await req.json() as Record<string, unknown>;
    } catch (err) {
      logger.warn('json_parse_failed', { route: 'search' }, err);
      return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
    }

    const limit = parseLimit(String(body.limit ?? '20'));

    if (typeof body.fingerprint_bits === 'string') {
      if (!/^[01]{2048}$/.test(body.fingerprint_bits)) {
        logger.info('validation_rejected', { route: 'search', field: 'fingerprint_bits', reason: 'shape' });
        return NextResponse.json({ error: 'fingerprint_bits must be exactly 2048 binary characters' }, { status: 400 });
      }
      const filters = parseFilters(body.filters);
      const results = await findSimilarCompounds(body.fingerprint_bits, limit, 0.4, filters).catch((err) => {
        logger.error('find_similar_compounds_failed', { route: 'search', limit }, err);
        throw err;
      });
      return NextResponse.json({ type: 'compound', results });
    }

    if (typeof body.rxn_fingerprint_bits === 'string') {
      if (!/^[01]{2048}$/.test(body.rxn_fingerprint_bits)) {
        logger.info('validation_rejected', { route: 'search', field: 'rxn_fingerprint_bits', reason: 'shape' });
        return NextResponse.json({ error: 'rxn_fingerprint_bits must be exactly 2048 binary characters' }, { status: 400 });
      }
      const results = await findSimilarReactions(body.rxn_fingerprint_bits, limit).catch((err) => {
        logger.error('find_similar_reactions_failed', { route: 'search', limit }, err);
        throw err;
      });
      return NextResponse.json({ type: 'reaction', results });
    }

    if (typeof body.smarts === 'string') {
      const smarts = body.smarts.trim();
      if (!smarts || smarts.length > MAX_SMARTS_LEN) {
        logger.info('validation_rejected', { route: 'search', field: 'smarts', reason: 'shape', length: smarts.length });
        return NextResponse.json({ error: 'smarts must be a non-empty string under 500 chars' }, { status: 400 });
      }
      try {
        const candidates = await listCompoundsForSubstructure(1000);
        const results: Array<{ id: string; smiles: string; canonSmiles: string | null; name: string | null; casNumber: string | null }> = [];
        let mcpFailures = 0;
        let consecutiveMcpFailures = 0;
        const MAX_CONSECUTIVE_MCP_FAILURES = 10;
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
            consecutiveMcpFailures = 0;
          } catch {
            // Skip individual failures; aggregate count for a single log line
            // at the end so a broken MCP doesn't masquerade as "no results".
            // A sustained burst of failures means the transport itself is
            // unstable — bail out with a 502 rather than silently truncating.
            mcpFailures++;
            consecutiveMcpFailures++;
            if (consecutiveMcpFailures >= MAX_CONSECUTIVE_MCP_FAILURES) {
              logger.error('substructure_mcp_unstable', {
                route: 'search',
                consecutive_failures: consecutiveMcpFailures,
                total_failures: mcpFailures,
                result_count: results.length,
              });
              return NextResponse.json(
                {
                  error: 'Substructure search MCP transport unstable',
                  detail: `${consecutiveMcpFailures} consecutive failures; partial results withheld`,
                },
                { status: 502 },
              );
            }
          }
        }
        if (mcpFailures > 0) {
          logger.warn('substructure_mcp_partial_failure', {
            route: 'search',
            candidate_count: candidates.length,
            failure_count: mcpFailures,
            result_count: results.length,
          });
        }
        return NextResponse.json({
          type: 'substructure',
          results,
          ...(mcpFailures > 0 ? { partial: true, mcp_failures: mcpFailures } : {}),
        });
      } catch (err) {
        logger.error('substructure_search_failed', { route: 'search', smarts_len: smarts.length }, err);
        return NextResponse.json({ error: (err as Error).message }, { status: 502 });
      }
    }

    logger.info('validation_rejected', { route: 'search', field: 'body', reason: 'no_recognized_query_field' });
    return NextResponse.json(
      { error: 'Provide fingerprint_bits (compound), rxn_fingerprint_bits (reaction), or smarts (substructure)' },
      { status: 400 },
    );
  });
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
