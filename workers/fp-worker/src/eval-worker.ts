import PgBoss from 'pg-boss';
import {
  db,
  sql,
  insertEvalRun,
  searchWikiByFTS,
  getWikiPage,
  countUnreadSubscriptions,
  pgRateLimit,
  type EvalProbeResult,
} from '@chemclaw2/db';

// BACKLOG #37 — scheduled regression eval. Probes the chemistry plumbing
// deterministically (no LLM calls, no $ cost per run) so the cron catches
// the cheap regressions: DB unreachable, FTS index dropped, query helpers
// crashing on empty inputs, rate-limit math flipped.
//
// LLM-level scoring against a golden chemistry-Q&A fixture set stays
// deferred (the BACKLOG note "deferred from v2.1 to keep scope tight" still
// applies). The scores JSONB shape is forward-compatible: a future LLM probe
// is just another { name, passed, durationMs, error? } entry.
//
// Cron: weekly. The runner writes one eval_runs row per execution; trend
// analysis is `SELECT started_at, fixtures_passed::float / fixtures_total
// FROM eval_runs ORDER BY started_at`.

type Probe = { name: string; run: () => Promise<void> };

const PROBES: Probe[] = [
  {
    name: 'db-reachable',
    run: async () => {
      const rows = await db.execute<{ one: number }>(sql`SELECT 1::int AS one`);
      if (rows[0]?.one !== 1) throw new Error('SELECT 1 returned unexpected shape');
    },
  },
  {
    name: 'wiki-fts-no-throw-on-empty-query',
    // FTS lookup over a string with no matches must return [] cleanly, not
    // throw. Catches the case where the to_tsvector index got dropped or the
    // wiki_pages.content_text column was renamed.
    run: async () => {
      const rows = await searchWikiByFTS('zzzzzzz_eval_probe_no_match_xx', 5);
      if (!Array.isArray(rows)) throw new Error('searchWikiByFTS did not return an array');
    },
  },
  {
    name: 'wiki-get-missing-slug-returns-null',
    // getWikiPage on a slug that cannot exist must return null/undefined, not
    // throw. Catches the case where the wiki_pages schema drifted.
    run: async () => {
      const row = await getWikiPage('__eval_probe_nonexistent_slug__');
      if (row !== undefined && row !== null) {
        throw new Error('getWikiPage returned a row for a nonexistent slug');
      }
    },
  },
  {
    name: 'unread-subscriptions-zero-for-fresh-user',
    // countUnreadSubscriptions for a user with no subscriptions must return 0
    // and not crash on the join. This is the helper the (app) layout calls
    // on every render — a regression here breaks the nav badge.
    run: async () => {
      const n = await countUnreadSubscriptions('__eval_probe_user__');
      if (n !== 0) throw new Error(`expected 0, got ${n}`);
    },
  },
  {
    name: 'rate-limit-boundary',
    // Round-trip pgRateLimit with a unique key. First call must be allowed.
    // BACKLOG #22 has unit-test coverage of the > vs >= math; this is the
    // integration variant (real DB, real ON CONFLICT path).
    run: async () => {
      const key = `__eval_probe_rl_${Date.now()}`;
      const r = await pgRateLimit(key, 5, 60_000);
      if (r.limited) throw new Error('first request reported as limited');
    },
  },
];

async function runProbe(p: Probe): Promise<EvalProbeResult> {
  const t0 = Date.now();
  try {
    await p.run();
    return { name: p.name, passed: true, durationMs: Date.now() - t0 };
  } catch (err) {
    return {
      name: p.name,
      passed: false,
      durationMs: Date.now() - t0,
      error: err instanceof Error ? err.message : String(err),
    };
  }
}

export async function runEvalOnce(): Promise<{ total: number; passed: number; id: string }> {
  const startedAt = new Date();
  const scores: EvalProbeResult[] = [];
  for (const p of PROBES) scores.push(await runProbe(p));
  const finishedAt = new Date();
  const id = await insertEvalRun({ startedAt, finishedAt, scores });
  const passed = scores.filter((s) => s.passed).length;
  if (passed < scores.length) {
    const failures = scores.filter((s) => !s.passed).map((s) => `${s.name}: ${s.error ?? '?'}`).join('; ');
    console.error(`[eval-worker] ${scores.length - passed}/${scores.length} probes failed: ${failures}`);
  } else {
    console.log(`[eval-worker] all ${scores.length} probes passed`);
  }
  return { total: scores.length, passed, id };
}

export async function startEvalWorker(boss: PgBoss): Promise<void> {
  await boss.createQueue('eval-regression', { policy: PgBoss.policies.stately } as PgBoss.Queue);
  // Weekly: Mondays at 04:00 UTC. Off-peak, well clear of fingerprint backlog
  // catch-up and the 03:23 feedback sweep.
  await boss.schedule('eval-regression', '0 4 * * 1');
  await boss.work('eval-regression', async () => {
    await runEvalOnce();
  });
}
