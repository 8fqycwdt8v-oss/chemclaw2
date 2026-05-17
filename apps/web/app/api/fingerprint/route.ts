import { auth } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { callMcpTool } from '@chemclaw2/agent-tools';
import { rateLimit } from '@/lib/rate-limit';
import { withApiContext } from '@/lib/api-context';
import { logger } from '@chemclaw2/observability';

const MAX_SMILES_LEN = 2000;

export async function POST(req: Request) {
  return withApiContext(async () => {
    const { userId } = await auth();
    if (!userId) {
      logger.info('auth_denied', { route: 'fingerprint' });
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const { limited } = await rateLimit(`fp:${userId}`, 30, 60_000);
    if (limited) {
      logger.warn('rate_limit_hit', { route: 'fingerprint', user_id: userId });
      return NextResponse.json({ error: 'Too many requests' }, { status: 429, headers: { 'Retry-After': '60' } });
    }

    let body: { kind?: unknown; smiles?: unknown };
    try {
      body = (await req.json()) as typeof body;
    } catch (err) {
      logger.warn('json_parse_failed', { route: 'fingerprint' }, err);
      return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
    }

    const kind = body.kind;
    const smiles = body.smiles;
    if (typeof smiles !== 'string' || smiles.length === 0 || smiles.length > MAX_SMILES_LEN) {
      logger.info('validation_rejected', { route: 'fingerprint', field: 'smiles', reason: 'shape', length: typeof smiles === 'string' ? smiles.length : -1 });
      return NextResponse.json({ error: 'smiles is required (≤2000 chars)' }, { status: 400 });
    }
    if (kind !== 'compound' && kind !== 'reaction') {
      logger.info('validation_rejected', { route: 'fingerprint', field: 'kind', reason: 'enum' });
      return NextResponse.json({ error: 'kind must be "compound" or "reaction"' }, { status: 400 });
    }

    const start = Date.now();
    try {
      const result =
        kind === 'reaction'
          ? await callMcpTool('mcp_rxnfp.server', 'compute_drfp', { reaction_smiles: smiles })
          : await callMcpTool('mcp_molfp.server', 'compute_morgan_fp', { smiles });
      const bits = result.fingerprint_bits;
      if (typeof bits !== 'string' || !/^[01]{2048}$/.test(bits)) {
        logger.error('fingerprint_invalid_output', { route: 'fingerprint', kind, duration_ms: Date.now() - start });
        return NextResponse.json({ error: 'Fingerprint computation returned invalid output' }, { status: 502 });
      }
      logger.info('fingerprint_computed', { route: 'fingerprint', kind, duration_ms: Date.now() - start });
      return NextResponse.json({ fingerprint_bits: bits });
    } catch (err) {
      logger.error('fingerprint_compute_failed', { route: 'fingerprint', kind, duration_ms: Date.now() - start }, err);
      return NextResponse.json({ error: (err as Error).message }, { status: 502 });
    }
  });
}
