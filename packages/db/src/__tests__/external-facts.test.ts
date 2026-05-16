import { describe, it, expect, vi, beforeEach } from 'vitest';

const mocks = vi.hoisted(() => ({
  insertCalls: [] as Array<{ values: Record<string, unknown>; conflictSet?: Record<string, unknown> }>,
  selectRows: [] as unknown[][],
}));

vi.mock('../client', () => ({
  db: {
    insert: () => ({
      values: (vals: Record<string, unknown>) => ({
        onConflictDoUpdate: (cfg: { set: Record<string, unknown> }) => {
          mocks.insertCalls.push({ values: vals, conflictSet: cfg.set });
          return Promise.resolve();
        },
      }),
    }),
    select: () => ({
      from: () => ({
        where: () => {
          const result = mocks.selectRows.shift() ?? [];
          // searchExternalFactsByFTS chains .orderBy().limit(); model as a chainable.
          const out = Object.assign(Promise.resolve(result), {
            orderBy: () => Object.assign(Promise.resolve(result), { limit: () => Promise.resolve(result) }),
          });
          return out;
        },
      }),
    }),
  },
}));

import {
  recordExternalFact,
  getExternalFact,
  searchExternalFactsByFTS,
} from '../queries/external-facts';

beforeEach(() => {
  mocks.insertCalls.length = 0;
  mocks.selectRows.length = 0;
});

describe('recordExternalFact', () => {
  it('issues an UPSERT with the (source_type, source_id) values', async () => {
    await recordExternalFact('eln', 'EXP-001', { yield: 75 }, 'user_alice');
    expect(mocks.insertCalls).toHaveLength(1);
    const call = mocks.insertCalls[0];
    expect(call.values).toMatchObject({
      sourceType: 'eln',
      sourceId: 'EXP-001',
      fetchedBy: 'user_alice',
    });
    expect(call.values.payload).toEqual({ yield: 75 });
  });

  it('preserves null contentText when caller omits it', async () => {
    await recordExternalFact('doc', 'https://x', { html: '...' }, 'user_x');
    expect(mocks.insertCalls[0].values.contentText).toBeNull();
  });

  it('refreshes last_seen and re-records payload on conflict', async () => {
    await recordExternalFact('web_search', 'aspirin synthesis', { results: [] }, 'user_x', 'aspirin synthesis results');
    const set = mocks.insertCalls[0].conflictSet ?? {};
    expect(set).toHaveProperty('payload');
    expect(set).toHaveProperty('lastSeen');
    expect(set).toHaveProperty('contentText');
    expect(set).toHaveProperty('fetchedBy');
  });
});

describe('getExternalFact', () => {
  it('returns null when no row matches', async () => {
    mocks.selectRows.push([]);
    expect(await getExternalFact('eln', 'no-such')).toBeNull();
  });

  it('returns the row when one matches', async () => {
    mocks.selectRows.push([{
      id: '11111111-1111-1111-1111-111111111111',
      sourceType: 'eln',
      sourceId: 'EXP-001',
      payload: { yield: 75 },
      contentText: null,
      firstSeen: new Date('2026-01-01T00:00:00Z'),
      lastSeen: new Date('2026-01-02T00:00:00Z'),
      fetchedBy: 'user_alice',
    }]);
    const row = await getExternalFact('eln', 'EXP-001');
    expect(row?.payload).toEqual({ yield: 75 });
    expect(row?.fetchedBy).toBe('user_alice');
  });
});

describe('searchExternalFactsByFTS', () => {
  it('returns ordered results from the FTS chain', async () => {
    const rows = [
      { id: 'a', sourceType: 'web_search', sourceId: 'q1', payload: {}, contentText: 'aspirin yield 75',
        firstSeen: new Date(), lastSeen: new Date(), fetchedBy: 'user_x' },
    ];
    mocks.selectRows.push(rows);
    const results = await searchExternalFactsByFTS('aspirin');
    expect(results).toHaveLength(1);
    expect(results[0].sourceType).toBe('web_search');
  });
});
