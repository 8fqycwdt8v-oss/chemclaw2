import { describe, it, expect, vi, beforeEach } from 'vitest';

const mocks = vi.hoisted(() => ({
  hits: [] as unknown[],
  capturedOpts: null as unknown,
  capturedQuery: null as string | null,
}));

vi.mock('@chemclaw2/db', () => ({
  lookupKnowledge: (query: string, opts: unknown) => {
    mocks.capturedQuery = query;
    mocks.capturedOpts = opts;
    return Promise.resolve(mocks.hits);
  },
}));

import { createLookupKnowledgeTool } from '../lookup-knowledge';

const noopEmbed = async () => Array(1536).fill(0);

beforeEach(() => {
  mocks.hits.length = 0;
  mocks.capturedOpts = null;
  mocks.capturedQuery = null;
});

describe('createLookupKnowledgeTool', () => {
  const tool = createLookupKnowledgeTool(noopEmbed);

  it('rejects an empty query without touching the DB', async () => {
    const r = await tool.execute({ query: '   ' });
    expect(r).toHaveProperty('error');
    expect(mocks.capturedQuery).toBeNull();
  });

  it('rejects an oversized query', async () => {
    const r = await tool.execute({ query: 'x'.repeat(501) });
    expect(r).toHaveProperty('error');
  });

  it('passes the embedFn through when semantic is not disabled', async () => {
    await tool.execute({ query: 'aspirin' });
    expect((mocks.capturedOpts as { embedFn?: unknown }).embedFn).toBe(noopEmbed);
  });

  it('omits the embedFn when semantic=false (lexical-only)', async () => {
    await tool.execute({ query: 'aspirin', semantic: false });
    expect((mocks.capturedOpts as { embedFn?: unknown }).embedFn).toBeUndefined();
  });

  it('forwards limit and types options', async () => {
    await tool.execute({ query: 'aspirin', limit: 5, types: ['paper', 'external'] });
    expect(mocks.capturedOpts).toMatchObject({ limit: 5, types: ['paper', 'external'] });
  });

  it('reshapes hits to drop bestRank from the agent-facing payload', async () => {
    mocks.hits.push({
      type: 'wiki',
      id: 'p1',
      title: 'Aspirin',
      excerpt: 'Aspirin is …',
      metadata: { slug: 'aspirin' },
      bestRank: 1,
    });
    const r = (await tool.execute({ query: 'aspirin' })) as
      { hits: Array<Record<string, unknown>>; count: number };
    expect(r.count).toBe(1);
    expect(r.hits[0]).toEqual({
      type: 'wiki',
      id: 'p1',
      title: 'Aspirin',
      excerpt: 'Aspirin is …',
      metadata: { slug: 'aspirin' },
    });
    expect(r.hits[0]).not.toHaveProperty('bestRank');
  });
});
