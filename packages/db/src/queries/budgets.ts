import { sql, eq } from 'drizzle-orm';
import { db } from '../client';
import { projectBudgets, projectBudgetSpend } from '../schema/budgets';

export type BudgetPeriod = 'day' | 'week' | 'month';

export type ProjectBudget = {
  projectKey: string;
  period: BudgetPeriod;
  toolCallsCap: number | null;
  experimentsCap: number | null;
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
  };
}

export async function upsertProjectBudget(
  projectKey: string,
  period: BudgetPeriod,
  caps: { toolCallsCap?: number | null; experimentsCap?: number | null },
  updatedBy: string,
): Promise<void> {
  await db
    .insert(projectBudgets)
    .values({
      projectKey,
      period,
      toolCallsCap: caps.toolCallsCap ?? null,
      experimentsCap: caps.experimentsCap ?? null,
      updatedBy,
    })
    .onConflictDoUpdate({
      target: projectBudgets.projectKey,
      set: {
        period,
        toolCallsCap: caps.toolCallsCap ?? null,
        experimentsCap: caps.experimentsCap ?? null,
        updatedBy,
        updatedAt: new Date(),
      },
    });
}

export type SpendRow = { toolCalls: number; experiments: number };

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
    })
    .from(projectBudgetSpend)
    .where(sql`${projectBudgetSpend.projectKey} = ${projectKey}
              AND ${projectBudgetSpend.periodStart} = ${periodStart.toISOString()}::timestamptz`);
  return row ?? { toolCalls: 0, experiments: 0 };
}

/**
 * Atomic increment via INSERT … ON CONFLICT DO UPDATE. Race-safe under
 * concurrent tool calls — Postgres serializes the conflicting writes.
 */
export async function incrementSpend(
  projectKey: string,
  period: BudgetPeriod,
  delta: { toolCalls?: number; experiments?: number },
  now: Date = new Date(),
): Promise<void> {
  const toolCalls = delta.toolCalls ?? 0;
  const experiments = delta.experiments ?? 0;
  if (toolCalls === 0 && experiments === 0) return;
  const periodStart = periodStartFor(period, now).toISOString();
  await db.execute(sql`
    INSERT INTO project_budget_spend (project_key, period_start, tool_calls, experiments, updated_at)
    VALUES (${projectKey}, ${periodStart}::timestamptz, ${toolCalls}, ${experiments}, NOW())
    ON CONFLICT (project_key, period_start) DO UPDATE
      SET tool_calls  = project_budget_spend.tool_calls  + EXCLUDED.tool_calls,
          experiments = project_budget_spend.experiments + EXCLUDED.experiments,
          updated_at  = NOW()
  `);
}

/**
 * Returns the first cap that the next planned increment would breach, or null
 * if the increment is within all caps. Callers use this in the PreToolUse hook
 * to short-circuit a tool that would push spend over the line.
 */
export async function checkBudgetWouldExceed(
  projectKey: string,
  plan: { toolCalls?: number; experiments?: number },
  now: Date = new Date(),
): Promise<{ exceeded: 'tool_calls' | 'experiments'; cap: number; current: number } | null> {
  const budget = await getProjectBudget(projectKey);
  if (!budget) return null; // no cap configured → unlimited

  const spend = await getCurrentSpend(projectKey, budget.period, now);
  const nextToolCalls = spend.toolCalls + (plan.toolCalls ?? 0);
  const nextExperiments = spend.experiments + (plan.experiments ?? 0);

  if (budget.toolCallsCap != null && nextToolCalls > budget.toolCallsCap) {
    return { exceeded: 'tool_calls', cap: budget.toolCallsCap, current: spend.toolCalls };
  }
  if (budget.experimentsCap != null && nextExperiments > budget.experimentsCap) {
    return { exceeded: 'experiments', cap: budget.experimentsCap, current: spend.experiments };
  }
  return null;
}
