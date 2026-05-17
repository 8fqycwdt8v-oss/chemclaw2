import { describe, it, expect, vi, beforeEach } from 'vitest';

const mocks = vi.hoisted(() => ({
  getWikiPage: vi.fn(),
  setCitationDisputed: vi.fn(),
  getCitationPair: vi.fn(),
  findChunksContainingCitationMarker: vi.fn(),
  recordContradiction: vi.fn(),
}));

vi.mock('@chemclaw2/db', () => ({
  getWikiPage: mocks.getWikiPage,
  setCitationDisputed: mocks.setCitationDisputed,
  getCitationPair: mocks.getCitationPair,
  findChunksContainingCitationMarker: mocks.findChunksContainingCitationMarker,
  recordContradiction: mocks.recordContradiction,
}));

import { createContradictionTools } from '../resolve-contradiction';

beforeEach(() => {
  mocks.getWikiPage.mockReset();
  mocks.setCitationDisputed.mockReset();
  mocks.getCitationPair.mockReset();
  mocks.findChunksContainingCitationMarker.mockReset();
  mocks.recordContradiction.mockReset();
});

describe('createContradictionTools.record', () => {
  const { record } = createContradictionTools('user_test');

  it('rejects an empty reason', async () => {
    const r = await record.execute({
      slug: 'aspirin',
      citation_a: '1',
      citation_b: '2',
      winner: 'a',
      reason: '',
    });
    expect(r).toHaveProperty('error');
    expect(mocks.getWikiPage).not.toHaveBeenCalled();
  });

  it('rejects a reason longer than 1000 chars', async () => {
    const r = await record.execute({
      slug: 'aspirin',
      citation_a: '1',
      citation_b: '2',
      winner: 'a',
      reason: 'x'.repeat(1001),
    });
    expect(r).toHaveProperty('error');
    expect(mocks.getWikiPage).not.toHaveBeenCalled();
  });

  it('returns an error when the wiki page is missing', async () => {
    mocks.getWikiPage.mockResolvedValueOnce(null);
    const r = await record.execute({
      slug: 'missing-page',
      citation_a: '1',
      citation_b: '2',
      winner: 'a',
      reason: 'evidence A is stronger',
    });
    expect(r).toEqual({ error: 'page not found' });
  });

  it('persists and marks the loser disputed for winner=a', async () => {
    mocks.getWikiPage.mockResolvedValueOnce({ id: 'page-1' });
    mocks.recordContradiction.mockResolvedValueOnce({ id: 'contradiction-1' });
    mocks.setCitationDisputed.mockResolvedValueOnce({ found: true });

    const r = (await record.execute({
      slug: 'aspirin',
      citation_a: '1',
      citation_b: '2',
      winner: 'a',
      reason: 'A is from a peer-reviewed source.',
    })) as Record<string, unknown>;

    expect(r.id).toBe('contradiction-1');
    expect(r.winner).toBe('a');
    expect(r.disputed_citation).toBe('2');
    expect(mocks.setCitationDisputed).toHaveBeenCalledWith('page-1', '2', true);
    expect(mocks.recordContradiction).toHaveBeenCalledWith(
      expect.objectContaining({
        pageId: 'page-1',
        citationA: '1',
        citationB: '2',
        proposedWinner: 'a',
        resolvedBy: 'user_test',
      }),
    );
  });

  it('does not mark anything disputed when winner=inconclusive', async () => {
    mocks.getWikiPage.mockResolvedValueOnce({ id: 'page-2' });
    mocks.recordContradiction.mockResolvedValueOnce({ id: 'contradiction-2' });

    const r = (await record.execute({
      slug: 'aspirin',
      citation_a: '1',
      citation_b: '2',
      winner: 'inconclusive',
      reason: 'Both sources have similar weight; needs more data.',
    })) as Record<string, unknown>;

    expect(r.id).toBe('contradiction-2');
    expect(r.winner).toBe('inconclusive');
    expect(r.disputed_citation).toBeNull();
    expect(mocks.setCitationDisputed).not.toHaveBeenCalled();
  });
});

describe('createContradictionTools.readTwo', () => {
  const { readTwo } = createContradictionTools('user_test');

  it('returns an error when the wiki page is missing', async () => {
    mocks.getWikiPage.mockResolvedValueOnce(null);
    const r = await readTwo.execute({ slug: 'missing', citation_a: '1', citation_b: '2' });
    expect(r).toEqual({ error: 'page not found' });
  });

  it('returns an error when one or both citations are missing on the page', async () => {
    mocks.getWikiPage.mockResolvedValueOnce({ id: 'page-1' });
    // Only one of the two citations exists
    mocks.getCitationPair.mockResolvedValueOnce([
      {
        citationId: '1',
        sourceType: 'doc',
        sourceId: null,
        label: 'paper A',
        disputed: false,
      },
    ]);
    const r = await readTwo.execute({ slug: 'aspirin', citation_a: '1', citation_b: '2' });
    expect(r).toEqual({ error: 'one or both citations not found on this page' });
  });
});
