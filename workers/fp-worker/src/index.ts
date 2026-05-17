import PgBoss from 'pg-boss';
import type { ChildProcess } from 'child_process';
import { db, compounds, reactions } from '@chemclaw2/db';
import { eq, sql } from 'drizzle-orm';
import { logger, installProcessHandlers } from '@chemclaw2/observability';
import { callMcpTool } from '@chemclaw2/agent-tools';
import { startCampaignWorker } from './campaign-worker';
import { startEvalWorker } from './eval-worker';

installProcessHandlers('fp-worker');

function logEnqueueFailure(queue: string, id: string) {
  return (err: unknown) => {
    logger.warn('fp_enqueue_failed', { queue, target_id: id }, err);
  };
}

const activeProcs = new Set<ChildProcess>();

const DATABASE_URL = process.env.DATABASE_URL;
if (!DATABASE_URL) throw new Error('DATABASE_URL is required');

const boss = new PgBoss(DATABASE_URL);
boss.on('error', (err) => logger.error('pg_boss_error', {}, err));

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
        const startMs = Date.now();
        try {
          const [compound] = await db.select().from(compounds).where(eq(compounds.id, id));
          if (!compound || compound.morganFp) continue;

          const result = await callMcpTool('mcp_molfp.server', 'compute_morgan_fp', { smiles: compound.smiles }, { activeProcs });

          const bits = result.fingerprint_bits;
          if (typeof bits !== 'string' || !/^[01]{2048}$/.test(bits)) {
            logger.error('fp_invalid_output', {
              kind: 'morgan',
              compound_id: id,
              expected_len: 2048,
              actual_len: typeof bits === 'string' ? bits.length : -1,
              actual_type: typeof bits,
            });
            throw new Error(`Invalid fingerprint_bits from MCP: expected 2048-char bit string, got ${typeof bits === 'string' ? `length ${bits.length}` : typeof bits}`);
          }

          await db
            .update(compounds)
            .set({
              morganFp: bits,
              fpComputedAt: new Date(),
            })
            .where(eq(compounds.id, id));
          logger.info('fp_computed', { kind: 'morgan', compound_id: id, duration_ms: Date.now() - startMs });
        } catch (err) {
          logger.error('fp_compute_failed', { kind: 'morgan', compound_id: id, duration_ms: Date.now() - startMs }, err);
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
        const startMs = Date.now();
        try {
          const [reaction] = await db.select().from(reactions).where(eq(reactions.id, id));
          if (!reaction || reaction.drfp) continue;

          const result = await callMcpTool('mcp_rxnfp.server', 'compute_drfp', { reaction_smiles: reaction.rxnSmiles }, { activeProcs });

          const bits = result.fingerprint_bits;
          if (typeof bits !== 'string' || !/^[01]{2048}$/.test(bits)) {
            logger.error('fp_invalid_output', {
              kind: 'drfp',
              reaction_id: id,
              expected_len: 2048,
              actual_len: typeof bits === 'string' ? bits.length : -1,
              actual_type: typeof bits,
            });
            throw new Error(`Invalid fingerprint_bits from MCP: expected 2048-char bit string, got ${typeof bits === 'string' ? `length ${bits.length}` : typeof bits}`);
          }

          await db
            .update(reactions)
            .set({
              drfp: bits,
              fpComputedAt: new Date(),
            })
            .where(eq(reactions.id, id));
          logger.info('fp_computed', { kind: 'drfp', reaction_id: id, duration_ms: Date.now() - startMs });
        } catch (err) {
          logger.error('fp_compute_failed', { kind: 'drfp', reaction_id: id, duration_ms: Date.now() - startMs }, err);
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
    const rows = await db.execute(sql`DELETE FROM rate_limits WHERE window_start < ${cutoff}`);
    // postgres-js exposes the affected count via the `count` property on the
    // result; expose it so a silently-no-op cron is visible.
    logger.info('sweep_rate_limits_complete', { deleted: (rows as unknown as { count?: number }).count ?? 0, cutoff_ms: cutoff });
  });

  // v2.1-A2: prune feedback older than the 1-year retention window. The trigger
  // from migration 0022 handles per-session cascades; this catches feedback that
  // outlives its session for any reason (manual session prune, restore, etc.).
  // agent_overrides is intentionally left alone — gate-override records are
  // compliance evidence and stay until the session they reference is deleted.
  await boss.createQueue('sweep-feedback', { policy: PgBoss.policies.stately } as PgBoss.Queue);
  await boss.schedule('sweep-feedback', '23 3 * * *');
  await boss.work('sweep-feedback', async () => {
    const rows = await db.execute(sql`DELETE FROM agent_feedback WHERE created_at < NOW() - INTERVAL '1 year'`);
    logger.info('sweep_feedback_complete', { deleted: (rows as unknown as { count?: number }).count ?? 0 });
  });

  // Catches rows inserted before the worker started or that lost a job to a crash.
  let lastPendingTotal = 0;
  let monotonicTicks = 0;
  async function pollMissingFingerprints() {
    const pendingCompounds = await db
      .select({ id: compounds.id })
      .from(compounds)
      .where(sql`morgan_fp IS NULL`)
      .limit(50);

    for (const { id } of pendingCompounds) {
      await boss.send('compute-morgan-fp', { id }, { singletonKey: id }).catch(logEnqueueFailure('compute-morgan-fp', id));
    }

    const pendingReactions = await db
      .select({ id: reactions.id })
      .from(reactions)
      .where(sql`drfp IS NULL`)
      .limit(50);

    for (const { id } of pendingReactions) {
      await boss.send('compute-drfp', { id }, { singletonKey: id }).catch(logEnqueueFailure('compute-drfp', id));
    }

    const pendingTotal = pendingCompounds.length + pendingReactions.length;
    logger.info('fp_poll_tick', {
      pending_compounds: pendingCompounds.length,
      pending_reactions: pendingReactions.length,
    });
    // Flag a stuck queue: 5 consecutive ticks (~2.5 min) where the backlog
    // failed to decrease, plus at least one job present. A healthy queue
    // drains; monotonic growth means an MCP child is hung or the worker is
    // blocked on something.
    if (pendingTotal > 0 && pendingTotal >= lastPendingTotal) {
      monotonicTicks++;
      if (monotonicTicks === 5) {
        logger.warn('fp_backlog_not_draining', { ticks: monotonicTicks, pending_total: pendingTotal });
      }
    } else {
      monotonicTicks = 0;
    }
    lastPendingTotal = pendingTotal;
  }

  await pollMissingFingerprints();
  // Wrap the async call so a synchronous throw before the first `await`
  // inside pollMissingFingerprints is also caught — a bare `.catch()` on the
  // returned promise misses sync throws and they bubble up as unhandled
  // rejections that may kill the worker (Node ≥15).
  const pollInterval = setInterval(() => {
    void (async () => {
      try {
        await pollMissingFingerprints();
      } catch (err) {
        logger.error('fp_poll_tick_failed', {}, err);
      }
    })();
  }, 30_000);

  // Liveness heartbeat. A worker that is alive but stuck (blocked on a hung
  // MCP child, deadlocked on a transaction) was previously indistinguishable
  // from a healthy one; this gives oncall a positive signal.
  const heartbeat = setInterval(() => {
    logger.info('worker_alive', { component: 'fp-worker', active_procs: activeProcs.size });
  }, 60_000);

  logger.info('worker_ready', { component: 'fp-worker' });

  process.on('SIGTERM', async () => {
    const shutdownStart = Date.now();
    logger.info('worker_shutdown_start', { component: 'fp-worker', active_procs: activeProcs.size });
    clearInterval(pollInterval);
    clearInterval(heartbeat);
    // SIGTERM first, then a grace window then SIGKILL — same escalation as
    // callMcpTool's timeout. A Python child mid-RDKit C call ignores SIGTERM
    // until the C frame returns, which can outlast the shutdown window.
    for (const proc of activeProcs) {
      try { proc.kill('SIGTERM'); } catch { /* already exited */ }
    }
    setTimeout(() => {
      for (const proc of activeProcs) {
        try { proc.kill('SIGKILL'); } catch { /* already exited */ }
      }
    }, 5_000).unref();
    await boss.stop();
    logger.info('worker_shutdown_complete', { component: 'fp-worker', duration_ms: Date.now() - shutdownStart });
    process.exit(0);
  });
}

start().catch((err) => {
  logger.error('worker_startup_failed', { component: 'fp-worker' }, err);
  process.exit(1);
});
