import { describe, it, expect, vi } from 'vitest';
import { similaritySearchTool } from '../similarity-search';

const BITS = '0'.repeat(2048);

describe('similaritySearchTool factory', () => {
  it('passes fingerprint bits, limit, and minScore through to the search fn', async () => {
    const search = vi.fn().mockResolvedValue([{ id: 'a' }]);
    const tool = similaritySearchTool({
      name: 'compound_similarity_search',
      description: 'd',
      fingerprintBitsDescription: 'm',
      search,
    });
    const r = await tool.execute({ fingerprint_bits: BITS, limit: 5, min_similarity: 0.7 });
    expect(r).toEqual({ results: [{ id: 'a' }] });
    expect(search).toHaveBeenCalledWith(BITS, 5, 0.7);
  });

  it('defaults limit=20 and min_similarity=defaultMin', async () => {
    const search = vi.fn().mockResolvedValue([]);
    const tool = similaritySearchTool({
      name: 't', description: 'd', fingerprintBitsDescription: 'm',
      defaultMin: 0.3, search,
    });
    await tool.execute({ fingerprint_bits: BITS });
    expect(search).toHaveBeenCalledWith(BITS, 20, 0.3);
  });

  it('default min_similarity is 0.4 when defaultMin is not provided', async () => {
    const search = vi.fn().mockResolvedValue([]);
    const tool = similaritySearchTool({
      name: 't', description: 'd', fingerprintBitsDescription: 'm', search,
    });
    await tool.execute({ fingerprint_bits: BITS });
    expect(search).toHaveBeenCalledWith(BITS, 20, 0.4);
  });

  it('routes thrown errors through toolError', async () => {
    const tool = similaritySearchTool({
      name: 'compound_similarity_search', description: 'd', fingerprintBitsDescription: 'm',
      search: async () => { throw new Error('hnsw down'); },
    });
    const r = await tool.execute({ fingerprint_bits: BITS });
    expect(r).toEqual({ error: 'hnsw down' });
  });

  it('propagates subagents tag onto the returned ToolDef', () => {
    const tool = similaritySearchTool({
      name: 't', description: 'd', fingerprintBitsDescription: 'm',
      subagents: ['deep-research'],
      search: async () => [],
    });
    expect(tool.subagents).toEqual(['deep-research']);
  });
});
