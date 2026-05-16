import { describe, it, expect } from 'vitest';
import { greenSolventTool } from '../green-solvent';

describe('greenSolventTool', () => {
  it('rejects empty input', async () => {
    const r = await greenSolventTool.execute({ solvents: [] });
    expect(r).toHaveProperty('error');
  });

  it('rejects > 20 solvents per call', async () => {
    const r = await greenSolventTool.execute({ solvents: Array(21).fill('O') });
    expect(r).toHaveProperty('error');
  });

  it('scores water as a top-rated solvent', async () => {
    const r = (await greenSolventTool.execute({ solvents: ['O'] })) as Record<string, unknown>;
    const results = r.results as Array<{ matched: boolean; mean_score: number; flagged_unsafe: boolean }>;
    expect(results[0].matched).toBe(true);
    expect(results[0].mean_score).toBeGreaterThanOrEqual(9);
    expect(results[0].flagged_unsafe).toBe(false);
  });

  it('flags DMF as red and supplies suggestions', async () => {
    const r = (await greenSolventTool.execute({ solvents: ['CN(C)C=O'] })) as Record<string, unknown>;
    const results = r.results as Array<{
      matched: boolean;
      flagged_unsafe: boolean;
      suggestions: Array<{ name: string }>;
    }>;
    expect(results[0].matched).toBe(true);
    expect(results[0].flagged_unsafe).toBe(true);
    expect(results[0].suggestions.length).toBe(5);
  });

  it('returns matched=false with no suggestions when SMILES is not in the table', async () => {
    const r = (await greenSolventTool.execute({ solvents: ['C(C(=O)O)N'] })) as Record<string, unknown>;
    const results = r.results as Array<{ matched: boolean; suggestions?: unknown }>;
    expect(results[0].matched).toBe(false);
    expect(results[0].suggestions).toBeUndefined();
  });

  it('is case- and whitespace-insensitive for the SMILES key', async () => {
    const r = (await greenSolventTool.execute({ solvents: ['  CCO  '] })) as Record<string, unknown>;
    const results = r.results as Array<{ matched: boolean; name: string }>;
    expect(results[0].matched).toBe(true);
    expect(results[0].name).toBe('ethanol');
  });
});
