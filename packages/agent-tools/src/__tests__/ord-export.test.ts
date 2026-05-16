import { describe, it, expect, vi } from 'vitest';

vi.mock('@chemclaw2/db', () => ({
  db: {
    select: () => ({
      from: () => ({
        where: () => Promise.resolve([
          {
            id: '11111111-1111-1111-1111-111111111111',
            rxnSmiles: 'CCO.O>>CC=O',
            name: 'test oxidation',
            conditions: 'air, RT',
            createdBy: 'user_xyz',
            createdAt: new Date('2026-01-01T00:00:00Z'),
          },
        ]),
      }),
    }),
  },
  reactions: {},
}));

import { exportReactionsAsOrd } from '../ord-export';

describe('exportReactionsAsOrd', () => {
  it('returns [] for empty input', async () => {
    expect(await exportReactionsAsOrd([])).toEqual([]);
  });

  it('rejects > 100 ids', async () => {
    await expect(exportReactionsAsOrd(Array(101).fill('id'))).rejects.toThrow();
  });

  it('produces ORD-shaped output with reactant + product entries', async () => {
    const [r] = await exportReactionsAsOrd(['11111111-1111-1111-1111-111111111111']);
    expect(r).not.toBeNull();
    const ord = r as Record<string, unknown>;
    expect(ord.reaction_id).toBe('11111111-1111-1111-1111-111111111111');
    expect((ord.identifiers as Array<Record<string, string>>)[0]).toEqual({
      type: 'REACTION_SMILES',
      value: 'CCO.O>>CC=O',
    });
    const inputs = ord.inputs as Array<Record<string, unknown>>;
    expect(inputs.length).toBe(2); // CCO and O
    const outcomes = ord.outcomes as Array<Record<string, unknown>>;
    expect(outcomes.length).toBe(1); // CC=O
  });

  it('returns null for missing ids', async () => {
    const result = await exportReactionsAsOrd([
      '11111111-1111-1111-1111-111111111111',
      '22222222-2222-2222-2222-222222222222',
    ]);
    expect(result[0]).not.toBeNull();
    expect(result[1]).toBeNull();
  });
});
