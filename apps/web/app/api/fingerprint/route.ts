import { z } from 'zod';
import { NextResponse } from 'next/server';
import { callMcpTool } from '@chemclaw2/agent-tools';
import { withRoute, errorResponse } from '@/lib/api-gate';
import { logger } from '@chemclaw2/observability';
import { randomUUID } from 'node:crypto';

const MAX_SMILES_LEN = 2000;

const FingerprintBody = z.object({
  kind: z.enum(['compound', 'reaction'], { message: 'kind must be "compound" or "reaction"' }),
  smiles: z.string().min(1).max(MAX_SMILES_LEN, 'smiles is required (≤2000 chars)'),
});

export const POST = withRoute(
  { rateLimit: { key: 'fp', max: 30, windowMs: 60_000 }, body: FingerprintBody },
  async ({ body }) => {
    try {
      const result =
        body.kind === 'reaction'
          ? await callMcpTool('mcp_rxnfp.server', 'compute_drfp', { reaction_smiles: body.smiles })
          : await callMcpTool('mcp_molfp.server', 'compute_morgan_fp', { smiles: body.smiles });
      const bits = result.fingerprint_bits;
      if (typeof bits !== 'string' || !/^[01]{2048}$/.test(bits)) {
        return errorResponse('Fingerprint computation returned invalid output', 502);
      }
      return NextResponse.json({ fingerprint_bits: bits });
    } catch (err) {
      // MCP errors include process paths / Python stack fragments; surface
      // only a correlation id to the client.
      const errorId = randomUUID();
      logger.error('fingerprint_compute_failed', { kind: body.kind, error_id: errorId }, err);
      return errorResponse('Fingerprint computation failed', 502, { errorId });
    }
  },
);
