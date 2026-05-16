import { describe, it, expect, vi, beforeEach } from 'vitest';

const mocks = vi.hoisted(() => ({
  insertCount: 0,
  insertedInputs: [] as unknown[],
  shouldThrow: null as string | null,
}));

vi.mock('@chemclaw2/db', () => ({
  insertProperties: async (inputs: unknown[]) => {
    if (mocks.shouldThrow) throw new Error(mocks.shouldThrow);
    mocks.insertedInputs = inputs;
    mocks.insertCount = inputs.length;
    return inputs.length;
  },
}));

import { createRegisterPropertyTool } from '../register-property';

const VALID_UUID = '11111111-1111-1111-1111-111111111111';

beforeEach(() => {
  mocks.insertCount = 0;
  mocks.insertedInputs = [];
  mocks.shouldThrow = null;
});

describe('createRegisterPropertyTool', () => {
  const tool = createRegisterPropertyTool('user_alice');

  it('rejects empty properties array without touching the DB', async () => {
    const r = await tool.execute({ properties: [] });
    expect(r).toEqual({ error: 'properties must be a non-empty array' });
    expect(mocks.insertCount).toBe(0);
  });

  it('rejects > 100 properties per call', async () => {
    const rows = Array(101).fill({ compound_id: VALID_UUID, name: 'yield', value_num: 75 });
    const r = await tool.execute({ properties: rows });
    expect(r).toHaveProperty('error');
    expect(mocks.insertCount).toBe(0);
  });

  it('rejects a non-UUID compound_id', async () => {
    const r = await tool.execute({
      properties: [{ compound_id: 'not-a-uuid', name: 'yield', value_num: 75 }],
    });
    expect((r as { error: string }).error).toMatch(/invalid compound_id/);
  });

  it('rejects a property with neither value_num nor value_text', async () => {
    const r = await tool.execute({
      properties: [{ compound_id: VALID_UUID, name: 'yield' }],
    });
    expect((r as { error: string }).error).toMatch(/value_num or non-empty value_text/);
  });

  it('rejects empty value_text as equivalent to missing', async () => {
    const r = await tool.execute({
      properties: [{ compound_id: VALID_UUID, name: 'note', value_text: '' }],
    });
    expect((r as { error: string }).error).toMatch(/value_num or non-empty value_text/);
  });

  it('rejects an invalid measured_at timestamp', async () => {
    const r = await tool.execute({
      properties: [{ compound_id: VALID_UUID, name: 'yield', value_num: 75, measured_at: 'yesterday' }],
    });
    expect((r as { error: string }).error).toMatch(/ISO-8601/);
  });

  it('inserts a happy-path batch and returns the count', async () => {
    const r = await tool.execute({
      properties: [
        { compound_id: VALID_UUID, name: 'yield', value_num: 75, unit: '%', source_citation_id: '1' },
        { compound_id: VALID_UUID, name: 'logP', value_num: 2.1, method: 'Crippen' },
      ],
    });
    expect(r).toEqual({ inserted: 2 });
    expect((mocks.insertedInputs[0] as Record<string, unknown>).compoundId).toBe(VALID_UUID);
    expect((mocks.insertedInputs[0] as Record<string, unknown>).sourceCitationId).toBe('1');
  });

  it('reshapes snake_case keys to camelCase for the DB layer', async () => {
    await tool.execute({
      properties: [{
        compound_id: VALID_UUID,
        name: 'yield',
        value_num: 75,
        source_citation_id: 'cite-7',
        measured_at: '2026-01-01T00:00:00Z',
      }],
    });
    const inserted = mocks.insertedInputs[0] as Record<string, unknown>;
    expect(inserted.compoundId).toBe(VALID_UUID);
    expect(inserted.valueNum).toBe(75);
    expect(inserted.sourceCitationId).toBe('cite-7');
    expect(inserted.measuredAt).toBeInstanceOf(Date);
  });

  it('returns the DB error message on failure (no partial-write claim)', async () => {
    mocks.shouldThrow = 'simulated DB outage';
    const r = await tool.execute({
      properties: [{ compound_id: VALID_UUID, name: 'yield', value_num: 75 }],
    });
    expect((r as { error: string }).error).toMatch(/simulated DB outage/);
  });
});
