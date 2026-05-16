import { describe, it, expect, vi, beforeEach } from 'vitest';

const mocks = vi.hoisted(() => ({
  budgetRow: null as null | Record<string, unknown>,
  spendRow: null as null | { toolCalls: number; experiments: number },
  executeCalls: [] as string[],
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
      return Promise.resolve([]);
    },
  },
}));

import {
  periodStartFor,
  getProjectBudget,
  getCurrentSpend,
  checkBudgetWouldExceed,
  incrementSpend,
} from '../queries/budgets';

beforeEach(() => {
  mocks.budgetRow = null;
  mocks.spendRow = null;
  mocks.executeCalls.length = 0;
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

describe('checkBudgetWouldExceed', () => {
  it('returns null when no budget is configured (unlimited)', async () => {
    mocks.budgetRow = null;
    const result = await checkBudgetWouldExceed('chemclaw2:user_x', { toolCalls: 1 });
    expect(result).toBeNull();
  });

  it('returns null when the cap is set but the increment fits', async () => {
    // Two consecutive select() calls: first returns budget, second returns spend (also empty here)
    mocks.budgetRow = {
      projectKey: 'chemclaw2:user_x',
      period: 'day',
      toolCallsCap: 100,
      experimentsCap: null,
    };
    const result = await checkBudgetWouldExceed('chemclaw2:user_x', { toolCalls: 1 });
    // First call returns budget; second call (for spend) also returns the budget
    // row shape since the mock select is global. But getCurrentSpend reads
    // { toolCalls, experiments } only, and the budget row doesn't have those
    // properties → undefined, which the helper coerces to zero behavior.
    expect(result).toBeNull();
  });

  it('reports exceeded when planned increment pushes over the cap', async () => {
    // Track a richer mock for this case: first call returns budget, second
    // returns spend at cap.
    let selectIdx = 0;
    const selectMock = vi.fn(() => ({
      from: () => ({
        where: () => {
          selectIdx += 1;
          if (selectIdx === 1) {
            return Promise.resolve([{
              projectKey: 'chemclaw2:user_x',
              period: 'day' as const,
              toolCallsCap: 100,
              experimentsCap: null,
            }]);
          }
          return Promise.resolve([{ toolCalls: 100, experiments: 0 }]);
        },
      }),
    }));
    const dbModule = await import('../client');
    (dbModule.db as unknown as { select: typeof selectMock }).select = selectMock;

    const result = await checkBudgetWouldExceed('chemclaw2:user_x', { toolCalls: 1 });
    expect(result).not.toBeNull();
    expect(result!.exceeded).toBe('tool_calls');
    expect(result!.cap).toBe(100);
    expect(result!.current).toBe(100);
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
