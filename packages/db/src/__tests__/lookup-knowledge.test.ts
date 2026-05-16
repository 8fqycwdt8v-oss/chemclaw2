import { describe, it, expect } from 'vitest';
import { rrfFuse, type KnowledgeHit } from '../queries/lookup-knowledge';

function hit(type: KnowledgeHit['type'], id: string, rank: number, title = id): KnowledgeHit {
  return { type, id, title, excerpt: '', metadata: {}, bestRank: rank };
}

describe('rrfFuse', () => {
  it('returns [] for no lists', () => {
    expect(rrfFuse([])).toEqual([]);
  });

  it('returns [] for all-empty lists', () => {
    expect(rrfFuse([[], [], []])).toEqual([]);
  });

  it('preserves order from a single list', () => {
    const list = [hit('wiki', 'a', 1), hit('wiki', 'b', 2), hit('wiki', 'c', 3)];
    const fused = rrfFuse([list]);
    expect(fused.map((h) => h.id)).toEqual(['a', 'b', 'c']);
  });

  it('boosts items that appear in multiple lists', () => {
    // "consensus" item appears in BOTH lists at rank 1; "wiki-only" appears
    // only in one. Consensus should rank first.
    const list1 = [hit('wiki', 'consensus', 1), hit('wiki', 'wiki-only', 2)];
    const list2 = [hit('paper', 'consensus', 1), hit('paper', 'paper-only', 2)];
    const fused = rrfFuse([list1, list2]);
    expect(fused[0].id).toBe('consensus');
  });

  it('keeps the same type+id from different lists as a single fused entry', () => {
    const list1 = [hit('wiki', 'X', 1)];
    const list2 = [hit('wiki', 'X', 1)]; // duplicate type+id
    const fused = rrfFuse([list1, list2]);
    expect(fused).toHaveLength(1);
  });

  it('treats same id across different types as distinct entries', () => {
    // type:id is the de-dupe key, so wiki:42 and paper:42 are different.
    const list = [hit('wiki', '42', 1), hit('paper', '42', 1)];
    const fused = rrfFuse([list]);
    expect(fused).toHaveLength(2);
  });

  it('places a rank-1-everywhere item ahead of a rank-1-once item even when scores are close', () => {
    // High-k makes individual rank contributions small; consensus still wins.
    const list1 = [hit('wiki', 'A', 1), hit('wiki', 'B', 2)];
    const list2 = [hit('paper', 'A', 1), hit('paper', 'C', 2)];
    const list3 = [hit('external', 'A', 1), hit('external', 'D', 2)];
    const fused = rrfFuse([list1, list2, list3]);
    expect(fused[0].id).toBe('A');
  });

  it('keeps the minimum bestRank when an item is fused from multiple lists', () => {
    const list1 = [hit('wiki', 'X', 5)];
    const list2 = [hit('wiki', 'X', 2)];
    const fused = rrfFuse([list1, list2]);
    expect(fused[0].bestRank).toBe(2);
  });
});
