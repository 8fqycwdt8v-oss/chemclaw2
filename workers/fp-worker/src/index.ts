import PgBoss from 'pg-boss';
import type { ChildProcess } from 'child_process';
import { db, compounds, reactions } from '@chemclaw2/db';
import { eq, sql } from 'drizzle-orm';
import { callMcpTool } from '@chemclaw2/agent-tools';
import { startCampaignWorker } from './campaign-worker';
import { startEvalWorker } from './eval-worker';

const activeProcs = new Set<ChildProcess>();

const DATABASE_URL = process.env.DATABASE_URL;
if (!DATABASE_URL) throw new Error('DATABASE_URL is required');

const boss = new PgBoss(DATABASE_URL);
boss.on('error', (err) => console.error('[pg-boss]', err));

async function start() {
  await boss.start();

  // stately policy makes singletonKey deduplicate pending+active jobs.
  await boss.createQueue('compute-morgan-fp', { policy: PgBoss.policies.stately } as PgBoss.Queue);
  await boss.createQueue('compute-drfp', { policy: PgBoss.policies.stately } as PgBoss.Queue);

  await boss.work<{ id: string }>(
    'compute-morgan-fp',
    { batchSize: 4, pollingIntervalSeconds: 10 + Math.floor(Math.random() * 5) },
    async (jobs) => {
      for (const job of jobs) {
        const { id } = job.data;
        try {
          const [compound] = await db.select().from(compounds).where(eq(compounds.id, id));
          if (!compound || compound.morganFp) continue;

          const result = await callMcpTool('mcp_molfp.server', 'compute_morgan_fp', { smiles: compound.smiles }, { activeProcs });

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

  await boss.work<{ id: string }>(
    'compute-drfp',
    { batchSize: 4, pollingIntervalSeconds: 10 + Math.floor(Math.random() * 5) },
    async (jobs) => {
      for (const job of jobs) {
        const { id } = job.data;
        try {
          const [reaction] = await db.select().from(reactions).where(eq(reactions.id, id));
          if (!reaction || reaction.drfp) continue;

          const result = await callMcpTool('mcp_rxnfp.server', 'compute_drfp', { reaction_smiles: reaction.rxnSmiles }, { activeProcs });

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

  // BACKLOG #37: weekly deterministic regression eval against the chemistry
  // plumbing. Writes one eval_runs row per execution.
  await startEvalWorker(boss);

  // Windows older than 2h are guaranteed-expired and safe to discard.
  await boss.createQueue('sweep-rate-limits', { policy: PgBoss.policies.stately } as PgBoss.Queue);
  await boss.schedule('sweep-rate-limits', '17 * * * *');
  await boss.work('sweep-rate-limits', async () => {
    const cutoff = Date.now() - 2 * 60 * 60 * 1000;
    await db.execute(sql`DELETE FROM rate_limits WHERE window_start < ${cutoff}`);
  });

  // v2.1-A2: prune feedback older than the 1-year retention window. The trigger
  // from migration 0022 handles per-session cascades; this catches feedback that
  // outlives its session for any reason (manual session prune, restore, etc.).
  // agent_overrides is intentionally left alone — gate-override records are
  // compliance evidence and stay until the session they reference is deleted.
  await boss.createQueue('sweep-feedback', { policy: PgBoss.policies.stately } as PgBoss.Queue);
  await boss.schedule('sweep-feedback', '23 3 * * *');
  await boss.work('sweep-feedback', async () => {
    await db.execute(sql`DELETE FROM agent_feedback WHERE created_at < NOW() - INTERVAL '1 year'`);
  });

  // Catches rows inserted before the worker started or that lost a job to a crash.
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
  const pollInterval = setInterval(
    () => pollMissingFingerprints().catch((err) => console.error('[fp-worker] pollMissingFingerprints error:', err)),
    30_000,
  );

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
