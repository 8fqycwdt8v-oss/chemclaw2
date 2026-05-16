import { sql, eq } from 'drizzle-orm';
import { db } from '../client';
import { projectBudgets, projectBudgetSpend } from '../schema/budgets';

export type BudgetPeriod = 'day' | 'week' | 'month';

export type ProjectBudget = {
  projectKey: string;
  period: BudgetPeriod;
  toolCallsCap: number | null;
  experimentsCap: number | null;
  // Wave-2c: LLM input+output tokens cap per period. Cache tokens excluded.
  tokensCap: number | null;
};

/**
 * Return the period-start timestamp for the budget's period at `now`. The Pre/
 * PostToolUse hooks call this to find which spend row to upsert against.
 *
 * Uses UTC midnight for day boundaries, ISO week start (Monday) for week, and
 * month-start for month. Postgres date_trunc agrees with these conventions.
 */
export function periodStartFor(period: BudgetPeriod, now: Date = new Date()): Date {
  const d = new Date(now);
  if (period === 'day') {
    d.setUTCHours(0, 0, 0, 0);
    return d;
  }
  if (period === 'week') {
    d.setUTCHours(0, 0, 0, 0);
    // 0 = Sunday … 1 = Monday; rewind to Monday.
    const day = d.getUTCDay();
    const diff = (day + 6) % 7;
    d.setUTCDate(d.getUTCDate() - diff);
    return d;
  }
  // month
  return new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), 1));
}

export async function getProjectBudget(projectKey: string): Promise<ProjectBudget | null> {
  const [row] = await db
    .select()
    .from(projectBudgets)
    .where(eq(projectBudgets.projectKey, projectKey));
  if (!row) return null;
  return {
    projectKey: row.projectKey,
    period: row.period as BudgetPeriod,
    toolCallsCap: row.toolCallsCap,
    experimentsCap: row.experimentsCap,
    tokensCap: row.tokensCap,
  };
}

export async function upsertProjectBudget(
  projectKey: string,
  period: BudgetPeriod,
  caps: { toolCallsCap?: number | null; experimentsCap?: number | null; tokensCap?: number | null },
  updatedBy: string,
): Promise<void> {
  await db
    .insert(projectBudgets)
    .values({
      projectKey,
      period,
      toolCallsCap: caps.toolCallsCap ?? null,
      experimentsCap: caps.experimentsCap ?? null,
      tokensCap: caps.tokensCap ?? null,
      updatedBy,
    })
    .onConflictDoUpdate({
      target: projectBudgets.projectKey,
      set: {
        period,
        toolCallsCap: caps.toolCallsCap ?? null,
        experimentsCap: caps.experimentsCap ?? null,
        tokensCap: caps.tokensCap ?? null,
        updatedBy,
        updatedAt: new Date(),
      },
    });
}

export type SpendRow = { toolCalls: number; experiments: number; tokens: number };

export async function getCurrentSpend(
  projectKey: string,
  period: BudgetPeriod,
  now: Date = new Date(),
): Promise<SpendRow> {
  const periodStart = periodStartFor(period, now);
  const [row] = await db
    .select({
      toolCalls: projectBudgetSpend.toolCalls,
      experiments: projectBudgetSpend.experiments,
      tokens: projectBudgetSpend.tokens,
    })
    .from(projectBudgetSpend)
    .where(sql`${projectBudgetSpend.projectKey} = ${projectKey}
              AND ${projectBudgetSpend.periodStart} = ${periodStart.toISOString()}::timestamptz`);
  return row ?? { toolCalls: 0, experiments: 0, tokens: 0 };
}

/**
 * Wave-1 D1: single round-trip variant. The agent's per-turn budget hook used
 * to issue three selects (`getProjectBudget` + `getCurrentSpend` from
 * `checkBudgetWouldExceed`, then `getProjectBudget` again from PostToolUse).
 * One LEFT JOIN returns both rows; the caller caches the result for the
 * lifetime of the query and PostToolUse re-uses the budget config to know
 * which period bucket to increment.
 */
export type BudgetWithSpend = {
  budget: ProjectBudget;
  spend: SpendRow;
};

export async function getBudgetWithSpend(
  projectKey: string,
  now: Date = new Date(),
): Promise<BudgetWithSpend | null> {
  // The period is on the budget row, but the spend-row period_start depends on
  // it — chicken-and-egg for a single SQL statement. We evaluate the period
  // in SQL via date_trunc/CASE so the same statement does both lookups.
  //
  // Wave-3f bug-fix: `date_trunc('week', $)` truncates in the SESSION timezone
  // by default. `periodStartFor` (TS) always computes in UTC. On a cluster
  // whose `timezone` GUC is not UTC the JOIN would point at a non-existent
  // (or wrong) period_start row, silently disagreeing with `incrementSpend`'s
  // later UPSERT key. Force UTC by truncating an `AT TIME ZONE 'UTC'`
  // expression — the result is a `timestamp` in UTC, which we re-cast back to
  // `timestamptz` for the comparison.
  const nowIso = now.toISOString();
  const rows = await db.execute<{
    period: string;
    tool_calls_cap: string | number | null;
    experiments_cap: number | null;
    tokens_cap: string | number | null;
    tool_calls: string | number | null;
    experiments: number | null;
    tokens: string | number | null;
  }>(sql`
    SELECT
      pb.period,
      pb.tool_calls_cap,
      pb.experiments_cap,
      pb.tokens_cap,
      pbs.tool_calls,
      pbs.experiments,
      pbs.tokens
    FROM project_budgets pb
    LEFT JOIN project_budget_spend pbs
      ON pbs.project_key = pb.project_key
     AND pbs.period_start = CASE pb.period
       WHEN 'day'   THEN date_trunc('day',   (${nowIso}::timestamptz) AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
       WHEN 'week'  THEN date_trunc('week',  (${nowIso}::timestamptz) AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
       WHEN 'month' THEN date_trunc('month', (${nowIso}::timestamptz) AT TIME ZONE 'UTC') AT TIME ZONE 'UTC'
     END
    WHERE pb.project_key = ${projectKey}
  `);
  const row = rows[0];
  if (!row) return null;
  return {
    budget: {
      projectKey,
      period: row.period as BudgetPeriod,
      toolCallsCap: row.tool_calls_cap == null ? null : Number(row.tool_calls_cap),
      experimentsCap: row.experiments_cap,
      tokensCap: row.tokens_cap == null ? null : Number(row.tokens_cap),
    },
    spend: {
      toolCalls: row.tool_calls == null ? 0 : Number(row.tool_calls),
      experiments: row.experiments ?? 0,
      tokens: row.tokens == null ? 0 : Number(row.tokens),
    },
  };
}

/**
 * Atomic increment via INSERT … ON CONFLICT DO UPDATE. Race-safe under
 * concurrent tool calls — Postgres serializes the conflicting writes.
 */
export async function incrementSpend(
  projectKey: string,
  period: BudgetPeriod,
  delta: { toolCalls?: number; experiments?: number; tokens?: number },
  now: Date = new Date(),
): Promise<void> {
  const toolCalls = delta.toolCalls ?? 0;
  const experiments = delta.experiments ?? 0;
  const tokens = delta.tokens ?? 0;
  if (toolCalls === 0 && experiments === 0 && tokens === 0) return;
  const periodStart = periodStartFor(period, now).toISOString();
  await db.execute(sql`
    INSERT INTO project_budget_spend (project_key, period_start, tool_calls, experiments, tokens, updated_at)
    VALUES (${projectKey}, ${periodStart}::timestamptz, ${toolCalls}, ${experiments}, ${tokens}, NOW())
    ON CONFLICT (project_key, period_start) DO UPDATE
      SET tool_calls  = project_budget_spend.tool_calls  + EXCLUDED.tool_calls,
          experiments = project_budget_spend.experiments + EXCLUDED.experiments,
          tokens      = project_budget_spend.tokens      + EXCLUDED.tokens,
          updated_at  = NOW()
  `);
}

// `checkBudgetWouldExceed` was removed in v2.2 Wave 1 D1. The check is now
// inlined in apps/web/lib/agent.ts's PreToolUse hook against a per-request
// cached `getBudgetWithSpend` result + an in-process localSpend counter, which
// cuts 3 round-trips per tool call down to 1 per request.
