import { describe, it, expect, vi, beforeEach } from 'vitest';

const mocks = vi.hoisted(() => ({
  upserted: [] as unknown[],
  returnId: 'paper-id-1',
  shouldThrow: null as string | null,
}));

vi.mock('@chemclaw2/db', () => ({
  upsertPaper: async (input: unknown) => {
    if (mocks.shouldThrow) throw new Error(mocks.shouldThrow);
    mocks.upserted.push(input);
    return { id: mocks.returnId };
  },
}));

import { createRegisterPaperTool } from '../register-paper';

beforeEach(() => {
  mocks.upserted = [];
  mocks.returnId = 'paper-id-1';
  mocks.shouldThrow = null;
});

describe('createRegisterPaperTool', () => {
  const tool = createRegisterPaperTool('user_alice');

  it('rejects empty title', async () => {
    const r = await tool.execute({ title: '' });
    expect(r).toEqual({ error: 'title must be 1-1000 chars' });
    expect(mocks.upserted).toHaveLength(0);
  });

  it('rejects oversize title', async () => {
    const r = await tool.execute({ title: 'x'.repeat(1001) });
    expect((r as { error: string }).error).toMatch(/1-1000 chars/);
  });

  it('rejects malformed DOI', async () => {
    const r = await tool.execute({ title: 'A paper', doi: 'not-a-doi' });
    expect((r as { error: string }).error).toMatch(/10\.NNNN/);
  });

  it('accepts a valid DOI', async () => {
    const r = await tool.execute({ title: 'A paper', doi: '10.1021/jacs.5b00123' });
    expect(r).toEqual({ id: 'paper-id-1', doi: '10.1021/jacs.5b00123', pubmed_id: null });
  });

  it('rejects non-numeric PubMed id', async () => {
    const r = await tool.execute({ title: 'A paper', pubmed_id: 'abc' });
    expect((r as { error: string }).error).toMatch(/numeric string/);
  });

  it('accepts a valid PubMed id', async () => {
    const r = await tool.execute({ title: 'A paper', pubmed_id: '12345678' });
    expect(r).toEqual({ id: 'paper-id-1', doi: null, pubmed_id: '12345678' });
  });

  it('rejects malformed URL', async () => {
    const r = await tool.execute({ title: 'A paper', url: 'not a url' });
    expect((r as { error: string }).error).toMatch(/valid URL/);
  });

  it('reshapes pubmed_id → pubmedId for the DB layer', async () => {
    await tool.execute({
      title: 'A paper',
      pubmed_id: '12345678',
      content_text: 'abstract body',
    });
    const upserted = mocks.upserted[0] as Record<string, unknown>;
    expect(upserted.pubmedId).toBe('12345678');
    expect(upserted.contentText).toBe('abstract body');
  });

  it('returns the DB error message on failure', async () => {
    mocks.shouldThrow = 'unique violation';
    const r = await tool.execute({ title: 'A paper' });
    expect((r as { error: string }).error).toMatch(/unique violation/);
  });
});
