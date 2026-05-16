import { desc } from 'drizzle-orm';
import { db } from '../client';
import { evalRuns, type EvalProbeResult } from '../schema/eval-runs';

export async function insertEvalRun(opts: {
  startedAt: Date;
  finishedAt: Date;
  scores: EvalProbeResult[];
  notes?: string;
}): Promise<string> {
  const passed = opts.scores.filter((s) => s.passed).length;
  const [row] = await db
    .insert(evalRuns)
    .values({
      startedAt: opts.startedAt,
      finishedAt: opts.finishedAt,
      fixturesTotal: opts.scores.length,
      fixturesPassed: passed,
      scores: opts.scores,
      notes: opts.notes,
    })
    .returning({ id: evalRuns.id });
  return row.id;
}

export async function listRecentEvalRuns(limit = 20) {
  return db
    .select()
    .from(evalRuns)
    .orderBy(desc(evalRuns.startedAt))
    .limit(Math.min(limit, 100));
}
