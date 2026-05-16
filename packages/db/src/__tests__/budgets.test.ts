import { describe, it, expect, vi, beforeEach } from 'vitest';

const mocks = vi.hoisted(() => ({
  budgetRow: null as null | Record<string, unknown>,
  spendRow: null as null | { toolCalls: number; experiments: number },
  executeCalls: [] as string[],
  executeRows: [] as unknown[][],
}));

vi.mock('../client', () => ({
  db: {
    select: () => ({
      from: () => ({
        where: () => Promise.resolve(mocks.budgetRow != null ? [mocks.budgetRow] : []),
      }),
    }),
    insert: () => ({
      values: () => ({
        onConflictDoUpdate: () => Promise.resolve(),
      }),
    }),
    execute: (sqlObj: { queryChunks?: unknown[] } | string) => {
      mocks.executeCalls.push(typeof sqlObj === 'string' ? sqlObj : 'sql-tagged');
      return Promise.resolve(mocks.executeRows.shift() ?? []);
    },
  },
}));

import {
  periodStartFor,
  getProjectBudget,
  getCurrentSpend,
  getBudgetWithSpend,
  incrementSpend,
} from '../queries/budgets';

beforeEach(() => {
  mocks.budgetRow = null;
  mocks.spendRow = null;
  mocks.executeCalls.length = 0;
  mocks.executeRows.length = 0;
});

describe('periodStartFor', () => {
  it('day rolls to UTC midnight', () => {
    const start = periodStartFor('day', new Date('2026-05-16T15:30:00Z'));
    expect(start.toISOString()).toBe('2026-05-16T00:00:00.000Z');
  });

  it('week rolls back to the most recent Monday', () => {
    // 2026-05-16 is a Saturday — Monday of that week is 2026-05-11
    const start = periodStartFor('week', new Date('2026-05-16T15:30:00Z'));
    expect(start.toISOString()).toBe('2026-05-11T00:00:00.000Z');
  });

  it('week of a Monday returns the same Monday', () => {
    const start = periodStartFor('week', new Date('2026-05-11T10:00:00Z'));
    expect(start.toISOString()).toBe('2026-05-11T00:00:00.000Z');
  });

  it('month rolls to first-of-month at UTC midnight', () => {
    const start = periodStartFor('month', new Date('2026-05-16T15:30:00Z'));
    expect(start.toISOString()).toBe('2026-05-01T00:00:00.000Z');
  });
});

describe('getProjectBudget', () => {
  it('returns null when no budget is configured', async () => {
    expect(await getProjectBudget('chemclaw2:user_x')).toBeNull();
  });

  it('returns the typed budget when a row exists', async () => {
    mocks.budgetRow = {
      projectKey: 'chemclaw2:user_x',
      period: 'day',
      toolCallsCap: 100,
      experimentsCap: 5,
    };
    const budget = await getProjectBudget('chemclaw2:user_x');
    expect(budget).toEqual({
      projectKey: 'chemclaw2:user_x',
      period: 'day',
      toolCallsCap: 100,
      experimentsCap: 5,
    });
  });
});

describe('getCurrentSpend', () => {
  it('returns zero when no spend row exists', async () => {
    // budgetRow is reused as the select target for spend in this mock; null = []
    mocks.budgetRow = null;
    const spend = await getCurrentSpend('chemclaw2:user_x', 'day');
    expect(spend).toEqual({ toolCalls: 0, experiments: 0 });
  });
});

describe('getBudgetWithSpend', () => {
  it('returns null when no budget row is configured', async () => {
    // execute returns [] from the empty queue → helper sees no row → null
    expect(await getBudgetWithSpend('chemclaw2:user_x')).toBeNull();
  });

  it('returns budget + zero spend when no spend row exists yet', async () => {
    mocks.executeRows.push([{
      period: 'day',
      tool_calls_cap: 100,
      experiments_cap: null,
      tool_calls: null,
      experiments: null,
    }]);
    const result = await getBudgetWithSpend('chemclaw2:user_x');
    expect(result).toEqual({
      budget: {
        projectKey: 'chemclaw2:user_x',
        period: 'day',
        toolCallsCap: 100,
        experimentsCap: null,
      },
      spend: { toolCalls: 0, experiments: 0 },
    });
  });

  it('returns the running spend when both rows are present', async () => {
    mocks.executeRows.push([{
      period: 'week',
      tool_calls_cap: 500,
      experiments_cap: 10,
      tool_calls: 42,
      experiments: 3,
    }]);
    const result = await getBudgetWithSpend('chemclaw2:user_x');
    expect(result?.budget.period).toBe('week');
    expect(result?.spend).toEqual({ toolCalls: 42, experiments: 3 });
  });

  it('coerces bigint-shaped numeric strings (Postgres BIGINT) to Number', async () => {
    // pg returns BIGINT as a string when above the JS safe range. The helper
    // must coerce to a Number for comparisons in the hook code to work.
    mocks.executeRows.push([{
      period: 'month',
      tool_calls_cap: '1000',
      experiments_cap: 50,
      tool_calls: '999',
      experiments: 49,
    }]);
    const result = await getBudgetWithSpend('chemclaw2:user_x');
    expect(result?.budget.toolCallsCap).toBe(1000);
    expect(result?.spend.toolCalls).toBe(999);
  });
});

describe('incrementSpend', () => {
  it('skips the DB write when delta is zero', async () => {
    await incrementSpend('chemclaw2:user_x', 'day', { toolCalls: 0, experiments: 0 });
    expect(mocks.executeCalls).toHaveLength(0);
  });

  it('issues an UPSERT when delta is non-zero', async () => {
    await incrementSpend('chemclaw2:user_x', 'day', { toolCalls: 1 });
    expect(mocks.executeCalls).toHaveLength(1);
  });
});
