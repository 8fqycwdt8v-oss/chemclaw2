import PgBoss from 'pg-boss';
import { spawn, ChildProcess } from 'child_process';
import { db } from '@chemclaw2/db';
import { compounds } from '@chemclaw2/db';
import { reactions } from '@chemclaw2/db';
import { eq, sql } from 'drizzle-orm';
import { startCampaignWorker } from './campaign-worker';

const activeProcs = new Set<ChildProcess>();

const DATABASE_URL = process.env.DATABASE_URL;
if (!DATABASE_URL) throw new Error('DATABASE_URL is required');

const boss = new PgBoss(DATABASE_URL);

boss.on('error', (err) => console.error('[pg-boss]', err));

const MCP_TIMEOUT_MS = 30_000;

/**
 * Call an MCP tool via stdio with the required 3-step handshake:
 *   1. Client sends `initialize`
 *   2. Server responds to `initialize`
 *   3. Client sends `notifications/initialized`
 *   4. Client sends `tools/call`
 *
 * Messages are newline-delimited JSON-RPC 2.0. Times out after 30 seconds.
 */
async function callMcpTool(
  module: string,
  toolName: string,
  args: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  return new Promise((resolve, reject) => {
    const proc = spawn('python', ['-m', module], { stdio: ['pipe', 'pipe', 'inherit'] });
    activeProcs.add(proc);

    let buffer = '';
    let initDone = false;
    let settled = false;
    const TOOL_CALL_ID = 2;

    const settle = (fn: () => void) => {
      if (settled) return;
      settled = true;
      activeProcs.delete(proc);
      clearTimeout(timer);
      fn();
    };

    const timer = setTimeout(() => {
      proc.kill();
      settle(() => reject(new Error(`MCP tool call timed out after ${MCP_TIMEOUT_MS}ms: ${toolName}`)));
    }, MCP_TIMEOUT_MS);

    const send = (msg: object) => {
      proc.stdin.write(JSON.stringify(msg) + '\n');
    };

    // Step 1: initialize handshake
    send({
      jsonrpc: '2.0',
      id: 1,
      method: 'initialize',
      params: {
        protocolVersion: '2024-11-05',
        capabilities: {},
        clientInfo: { name: 'fp-worker', version: '1.0' },
      },
    });

    proc.stdout.on('data', (chunk: Buffer) => {
      buffer += chunk.toString();
      const lines = buffer.split('\n');
      buffer = lines.pop() ?? '';

      for (const line of lines) {
        if (!line.trim()) continue;
        let msg: Record<string, unknown>;
        try {
          msg = JSON.parse(line) as Record<string, unknown>;
        } catch {
          continue;
        }

        if (!initDone && (msg as { id?: number }).id === 1) {
          // Step 2: initialize response received — complete handshake then send tool call
          initDone = true;
          send({ jsonrpc: '2.0', method: 'notifications/initialized', params: {} }); // Step 3
          send({ jsonrpc: '2.0', id: TOOL_CALL_ID, method: 'tools/call', params: { name: toolName, arguments: args } });
        } else if ((msg as { id?: number }).id === TOOL_CALL_ID) {
          proc.stdin.end();
          try {
            const result = msg as { result?: { content?: Array<{ text?: string }> } };
            const text = result.result?.content?.[0]?.text;
            const parsed = text ? (JSON.parse(text) as Record<string, unknown>) : (msg.result as Record<string, unknown>);
            settle(() => resolve(parsed));
          } catch {
            settle(() => reject(new Error(`Failed to parse MCP response: ${line}`)));
          }
        }
      }
    });

    proc.on('close', (code) => {
      if (code !== 0) settle(() => reject(new Error(`MCP process exited with code ${code}`)));
      else settle(() => reject(new Error('MCP process closed before tool response')));
    });

    proc.on('error', (err) => {
      settle(() => reject(err));
    });
  });
}

async function start() {
  await boss.start();

  // Create queues with 'stately' policy so singletonKey actually prevents
  // duplicate pending/active jobs. createQueue is idempotent (ON CONFLICT DO NOTHING).
  await boss.createQueue('compute-morgan-fp', { policy: PgBoss.policies.stately } as PgBoss.Queue);
  await boss.createQueue('compute-drfp', { policy: PgBoss.policies.stately } as PgBoss.Queue);

  // Process Morgan fingerprint jobs
  await boss.work<{ id: string }>(
    'compute-morgan-fp',
    { batchSize: 4, pollingIntervalSeconds: 10 + Math.floor(Math.random() * 5) },
    async (jobs) => {
      for (const job of jobs) {
        const { id } = job.data;
        try {
          const [compound] = await db.select().from(compounds).where(eq(compounds.id, id));
          if (!compound || compound.morganFp) continue;

          const result = await callMcpTool('mcp_molfp.server', 'compute_morgan_fp', {
            smiles: compound.smiles,
          });

          const bits = result.fingerprint_bits;
          if (typeof bits !== 'string' || !/^[01]{2048}$/.test(bits)) {
            throw new Error(`Invalid fingerprint_bits from MCP: expected 2048-char bit string, got ${typeof bits === 'string' ? `length ${bits.length}` : typeof bits}`);
          }

          await db
            .update(compounds)
            .set({
              morganFp: bits,
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

  // Process DRFP jobs
  await boss.work<{ id: string }>(
    'compute-drfp',
    { batchSize: 4, pollingIntervalSeconds: 10 + Math.floor(Math.random() * 5) },
    async (jobs) => {
      for (const job of jobs) {
        const { id } = job.data;
        try {
          const [reaction] = await db.select().from(reactions).where(eq(reactions.id, id));
          if (!reaction || reaction.drfp) continue;

          const result = await callMcpTool('mcp_rxnfp.server', 'compute_drfp', {
            reaction_smiles: reaction.rxnSmiles,
          });

          const bits = result.fingerprint_bits;
          if (typeof bits !== 'string' || !/^[01]{2048}$/.test(bits)) {
            throw new Error(`Invalid fingerprint_bits from MCP: expected 2048-char bit string, got ${typeof bits === 'string' ? `length ${bits.length}` : typeof bits}`);
          }

          await db
            .update(reactions)
            .set({
              drfp: bits,
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

  await startCampaignWorker(boss);

  // Poll every 30s for rows without fingerprints (catches inserts that happened
  // before this worker started). singletonKey + stately policy prevents duplicates.
  async function pollMissingFingerprints() {
    const pendingCompounds = await db
      .select({ id: compounds.id })
      .from(compounds)
      .where(sql`morgan_fp IS NULL`)
      .limit(50);

    for (const { id } of pendingCompounds) {
      await boss.send('compute-morgan-fp', { id }, { singletonKey: id }).catch(() => {});
    }

    const pendingReactions = await db
      .select({ id: reactions.id })
      .from(reactions)
      .where(sql`drfp IS NULL`)
      .limit(50);

    for (const { id } of pendingReactions) {
      await boss.send('compute-drfp', { id }, { singletonKey: id }).catch(() => {});
    }
  }

  await pollMissingFingerprints();
  const pollInterval = setInterval(pollMissingFingerprints, 30_000);

  console.log('[fp-worker] ready — processing compute-morgan-fp and compute-drfp jobs');

  process.on('SIGTERM', async () => {
    clearInterval(pollInterval);
    for (const proc of activeProcs) proc.kill();
    await boss.stop();
    process.exit(0);
  });
}

start().catch((err) => {
  console.error('[fp-worker] startup failed:', err);
  process.exit(1);
});
