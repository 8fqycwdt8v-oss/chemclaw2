import { auth } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { callMcpTool } from '@chemclaw2/agent-tools';
import { rateLimit } from '@/lib/rate-limit';

const MAX_SMILES_LEN = 2000;

export async function POST(req: Request) {
  const { userId } = await auth();
  if (!userId) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const { limited } = await rateLimit(`fp:${userId}`, 30, 60_000);
  if (limited) {
    return NextResponse.json({ error: 'Too many requests' }, { status: 429, headers: { 'Retry-After': '60' } });
  }

  let body: { kind?: unknown; smiles?: unknown };
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
  }

  const kind = body.kind;
  const smiles = body.smiles;
  if (typeof smiles !== 'string' || smiles.length === 0 || smiles.length > MAX_SMILES_LEN) {
    return NextResponse.json({ error: 'smiles is required (≤2000 chars)' }, { status: 400 });
  }
  if (kind !== 'compound' && kind !== 'reaction') {
    return NextResponse.json({ error: 'kind must be "compound" or "reaction"' }, { status: 400 });
  }

  try {
    const result =
      kind === 'reaction'
        ? await callMcpTool('mcp_rxnfp.server', 'compute_drfp', { reaction_smiles: smiles })
        : await callMcpTool('mcp_molfp.server', 'compute_morgan_fp', { smiles });
    const bits = result.fingerprint_bits;
    if (typeof bits !== 'string' || !/^[01]{2048}$/.test(bits)) {
      return NextResponse.json({ error: 'Fingerprint computation returned invalid output' }, { status: 502 });
    }
    return NextResponse.json({ fingerprint_bits: bits });
  } catch (err) {
    return NextResponse.json({ error: (err as Error).message }, { status: 502 });
  }
}
