import { describe, it, expect, vi, beforeEach } from 'vitest';

const mocks = vi.hoisted(() => ({
  rows: [] as unknown[],
  listCalls: [] as Array<{ compoundId: string; filters: unknown; limit?: number }>,
}));

vi.mock('@chemclaw2/db', () => ({
  listPropertiesForCompound: (compoundId: string, filters: unknown, limit?: number) => {
    mocks.listCalls.push({ compoundId, filters, limit });
    return Promise.resolve(mocks.rows);
  },
}));

import { lookupPropertiesTool } from '../lookup-properties';

const VALID_UUID = '11111111-1111-1111-1111-111111111111';

beforeEach(() => {
  mocks.rows.length = 0;
  mocks.listCalls.length = 0;
});

describe('lookupPropertiesTool', () => {
  it('rejects a non-UUID compound_id without hitting the DB', async () => {
    const r = await lookupPropertiesTool.execute({ compound_id: 'not-a-uuid' });
    expect(r).toHaveProperty('error');
    expect(mocks.listCalls).toHaveLength(0);
  });

  it('rejects an inverted numeric range', async () => {
    const r = await lookupPropertiesTool.execute({
      compound_id: VALID_UUID,
      value_num_gte: 100,
      value_num_lte: 50,
    });
    expect(r).toEqual({ error: 'value_num_gte must be ≤ value_num_lte' });
    expect(mocks.listCalls).toHaveLength(0);
  });

  it('forwards all filters to the query helper', async () => {
    await lookupPropertiesTool.execute({
      compound_id: VALID_UUID,
      name: 'yield',
      unit: '%',
      value_num_gte: 60,
      value_num_lte: 100,
      limit: 25,
    });
    expect(mocks.listCalls).toHaveLength(1);
    expect(mocks.listCalls[0]).toEqual({
      compoundId: VALID_UUID,
      filters: { name: 'yield', unit: '%', valueNumGte: 60, valueNumLte: 100 },
      limit: 25,
    });
  });

  it('reshapes rows from camelCase to snake_case for the agent', async () => {
    mocks.rows.push({
      id: 'p1',
      compoundId: VALID_UUID,
      name: 'yield',
      valueNum: 75,
      valueText: null,
      unit: '%',
      method: 'HPLC',
      sourceCitationId: 'cite-1',
      measuredAt: new Date('2026-01-01T00:00:00Z'),
      createdAt: new Date(),
      createdBy: 'user_x',
    });
    const r = (await lookupPropertiesTool.execute({ compound_id: VALID_UUID })) as
      { rows: Array<Record<string, unknown>>; count: number };
    expect(r.count).toBe(1);
    expect(r.rows[0]).toEqual({
      id: 'p1',
      name: 'yield',
      value_num: 75,
      value_text: null,
      unit: '%',
      method: 'HPLC',
      source_citation_id: 'cite-1',
      measured_at: new Date('2026-01-01T00:00:00Z'),
    });
  });

  it('returns count: 0 when the helper yields no rows', async () => {
    const r = (await lookupPropertiesTool.execute({ compound_id: VALID_UUID })) as
      { count: number };
    expect(r.count).toBe(0);
  });
});
