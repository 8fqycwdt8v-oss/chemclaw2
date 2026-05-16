import { describe, it, expect, vi, beforeEach } from 'vitest';

const mocks = vi.hoisted(() => ({
  insertedValues: [] as unknown[],
  selectRows: [] as unknown[][],
}));

vi.mock('../client', () => ({
  db: {
    insert: () => ({
      values: (vals: unknown) => ({
        returning: () => {
          if (Array.isArray(vals)) {
            mocks.insertedValues.push(...vals);
            return Promise.resolve(vals.map((_, i) => ({ id: `id-${i}` })));
          }
          mocks.insertedValues.push(vals);
          return Promise.resolve([{ id: 'id-0' }]);
        },
      }),
    }),
    select: () => ({
      from: () => ({
        where: () => ({
          orderBy: () => ({
            limit: () => Promise.resolve(mocks.selectRows.shift() ?? []),
          }),
        }),
      }),
    }),
  },
}));

import {
  insertProperty,
  insertProperties,
  listPropertiesForCompound,
} from '../queries/properties';

const COMPOUND = '11111111-1111-1111-1111-111111111111';

beforeEach(() => {
  mocks.insertedValues.length = 0;
  mocks.selectRows.length = 0;
});

describe('insertProperty', () => {
  it('rejects when neither valueNum nor valueText is provided', async () => {
    await expect(
      insertProperty({ compoundId: COMPOUND, name: 'yield' }, 'user_x'),
    ).rejects.toThrow(/valueNum or non-empty valueText/);
  });

  it('rejects empty valueText as equivalent to missing', async () => {
    await expect(
      insertProperty({ compoundId: COMPOUND, name: 'note', valueText: '' }, 'user_x'),
    ).rejects.toThrow(/valueText/);
  });

  it('rejects names outside the 1-200 char window', async () => {
    await expect(
      insertProperty({ compoundId: COMPOUND, name: '', valueNum: 75 }, 'user_x'),
    ).rejects.toThrow(/1-200/);
    await expect(
      insertProperty({ compoundId: COMPOUND, name: 'x'.repeat(201), valueNum: 75 }, 'user_x'),
    ).rejects.toThrow(/1-200/);
  });

  it('inserts a single property with all optional fields', async () => {
    const result = await insertProperty({
      compoundId: COMPOUND,
      name: 'yield',
      valueNum: 75.5,
      unit: '%',
      method: 'HPLC',
      sourceCitationId: 'cite-1',
      measuredAt: new Date('2026-01-01T00:00:00Z'),
    }, 'user_alice');
    expect(result.id).toBe('id-0');
    expect(mocks.insertedValues[0]).toMatchObject({
      compoundId: COMPOUND,
      name: 'yield',
      valueNum: 75.5,
      unit: '%',
      createdBy: 'user_alice',
    });
  });
});

describe('insertProperties (bulk)', () => {
  it('short-circuits to 0 for empty input without hitting the DB', async () => {
    expect(await insertProperties([], 'user_x')).toBe(0);
    expect(mocks.insertedValues).toHaveLength(0);
  });

  it('rejects the entire batch when any row is missing both values', async () => {
    await expect(insertProperties([
      { compoundId: COMPOUND, name: 'yield', valueNum: 75 },
      { compoundId: COMPOUND, name: 'note' },
    ], 'user_x')).rejects.toThrow(/every property/);
  });

  it('inserts every row in one round-trip', async () => {
    const n = await insertProperties([
      { compoundId: COMPOUND, name: 'yield', valueNum: 75 },
      { compoundId: COMPOUND, name: 'logP', valueNum: 2.1 },
    ], 'user_alice');
    expect(n).toBe(2);
    expect(mocks.insertedValues).toHaveLength(2);
  });
});

describe('listPropertiesForCompound', () => {
  it('passes through whatever the DB returns', async () => {
    const sample = [{
      id: 'a', compoundId: COMPOUND, name: 'yield', valueNum: 75, valueText: null,
      unit: '%', method: null, sourceCitationId: null, measuredAt: null,
      createdAt: new Date(), createdBy: 'user_x',
    }];
    mocks.selectRows.push(sample);
    const rows = await listPropertiesForCompound(COMPOUND, { name: 'yield' });
    expect(rows).toEqual(sample);
  });
});
