import { describe, it, expect, vi, beforeEach } from 'vitest';

// BACKLOG #22 — the source comment in queries/rate-limit.ts spells out that
// the count == maxRequests case must be allowed and only count > maxRequests
// blocks. A prior bug had this inverted; the test pins the boundary so a
// regression flips back to >= it'll fail here, not in production.

const mocks = vi.hoisted(() => ({
  returnCount: 1 as number,
  shouldThrow: false as boolean,
}));

vi.mock('../client', () => ({
  db: {
    insert: () => ({
      values: () => ({
        onConflictDoUpdate: () => ({
          returning: () => {
            if (mocks.shouldThrow) throw new Error('db down');
            return Promise.resolve([{ count: mocks.returnCount }]);
          },
        }),
      }),
    }),
  },
}));

import { pgRateLimit } from '../queries/rate-limit';

beforeEach(() => {
  mocks.returnCount = 1;
  mocks.shouldThrow = false;
});

describe('pgRateLimit boundary', () => {
  it('first request (count=1) is not limited when max=5', async () => {
    mocks.returnCount = 1;
    const r = await pgRateLimit('k', 5, 60_000);
    expect(r.limited).toBe(false);
  });

  it('exactly-at-limit (count == maxRequests) is NOT limited', async () => {
    mocks.returnCount = 5;
    const r = await pgRateLimit('k', 5, 60_000);
    expect(r.limited).toBe(false);
  });

  it('one-over-limit (count == maxRequests + 1) IS limited', async () => {
    mocks.returnCount = 6;
    const r = await pgRateLimit('k', 5, 60_000);
    expect(r.limited).toBe(true);
  });

  it('far over limit stays limited', async () => {
    mocks.returnCount = 999;
    const r = await pgRateLimit('k', 5, 60_000);
    expect(r.limited).toBe(true);
  });

  it('max=1 — first request allowed, second blocked', async () => {
    mocks.returnCount = 1;
    expect((await pgRateLimit('k', 1, 60_000)).limited).toBe(false);
    mocks.returnCount = 2;
    expect((await pgRateLimit('k', 1, 60_000)).limited).toBe(true);
  });

  it('DB failure fails CLOSED — a stampede that stresses the DB is precisely when rate limiting matters most', async () => {
    mocks.shouldThrow = true;
    const r = await pgRateLimit('k', 5, 60_000);
    expect(r.limited).toBe(true);
  });
});
