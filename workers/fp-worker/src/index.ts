import PgBoss from 'pg-boss';
import { spawn } from 'child_process';
import { db } from '@chemclaw2/db';
import { compounds } from '@chemclaw2/db';
import { reactions } from '@chemclaw2/db';
import { eq, sql } from 'drizzle-orm';

const DATABASE_URL = process.env.DATABASE_URL;
if (!DATABASE_URL) throw new Error('DATABASE_URL is required');

const boss = new PgBoss(DATABASE_URL);

boss.on('error', (err) => console.error('[pg-boss]', err));

async function callMcpTool(
  module: string,
  toolName: string,
  args: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    const proc = spawn('python', ['-m', module], { stdio: ['pipe', 'pipe', 'inherit'] });
    const request = JSON.stringify({
      jsonrpc: '2.0',
      id: 1,
      method: 'tools/call',
      params: { name: toolName, arguments: args },
    });
    let stdout = '';
    proc.stdout.on('data', (d: Buffer) => (stdout += d.toString()));
    proc.on('close', (code) => {
      if (code !== 0) return reject(new Error(`MCP process exited with code ${code}`));
      try {
        const parsed = JSON.parse(stdout);
        resolve(parsed.result?.content?.[0]?.text ? JSON.parse(parsed.result.content[0].text) : parsed.result);
      } catch (e) {
        reject(new Error(`Failed to parse MCP response: ${stdout}`));
      }
    });
    proc.stdin.write(request + '\n');
    proc.stdin.end();
  });
}

async function start() {
  await boss.start();

  // Compute Morgan fingerprint for compounds inserted without one
  await boss.work<{ id: string }>(
    'compute-morgan-fp',
    { batchSize: 4 },
    async (jobs) => {
      for (const job of jobs) {
        const { id } = job.data;
        try {
          const [compound] = await db.select().from(compounds).where(eq(compounds.id, id));
          if (!compound || compound.morganFp) continue;

          const result = await callMcpTool('mcp_molfp.server', 'compute_morgan_fp', {
            smiles: compound.smiles,
          });

          await db
            .update(compounds)
            .set({
              morganFp: result.fingerprint_hex as string,
              canonSmiles: (result.canonical_smiles as string | undefined) ?? compound.canonSmiles,
              fpComputedAt: new Date(),
            })
            .where(eq(compounds.id, id));
        } catch (err) {
          console.error(`[fp-worker] compute-morgan-fp failed for ${id}:`, err);
          throw err; // pg-boss will retry
        }
      }
    },
  );

  // Compute DRFP for reactions inserted without one
  await boss.work<{ id: string }>(
    'compute-drfp',
    { batchSize: 4 },
    async (jobs) => {
      for (const job of jobs) {
        const { id } = job.data;
        try {
          const [reaction] = await db.select().from(reactions).where(eq(reactions.id, id));
          if (!reaction || reaction.drfp) continue;

          const result = await callMcpTool('mcp_rxnfp.server', 'compute_drfp', {
            reaction_smiles: reaction.rxnSmiles,
          });

          await db
            .update(reactions)
            .set({
              drfp: result.fingerprint_hex as string,
              fpComputedAt: new Date(),
            })
            .where(eq(reactions.id, id));
        } catch (err) {
          console.error(`[fp-worker] compute-drfp failed for ${id}:`, err);
          throw err;
        }
      }
    },
  );

  console.log('[fp-worker] ready — processing compute-morgan-fp and compute-drfp jobs');

  // Graceful shutdown
  process.on('SIGTERM', async () => {
    await boss.stop();
    process.exit(0);
  });
}

start().catch((err) => {
  console.error('[fp-worker] startup failed:', err);
  process.exit(1);
});
