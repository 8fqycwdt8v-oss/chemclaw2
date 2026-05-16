import { describe, it, expect } from 'vitest';
import { chunkText } from '../queries/wiki';

describe('chunkText', () => {
  it('emits one chunk for short content', () => {
    const chunks = chunkText('Aspirin is a salicylate drug used to reduce pain.');
    expect(chunks).toHaveLength(1);
    expect(chunks[0]).toMatch(/aspirin/i);
  });

  it('drops fragments shorter than 11 chars', () => {
    expect(chunkText('hi.')).toEqual([]);
  });

  it('splits long paragraphs on sentence boundaries', () => {
    const longSentences = Array.from({ length: 30 }, (_, i) =>
      `Sentence number ${i} contains some chemistry like SMILES CCO and a few more words.`,
    ).join(' ');
    const chunks = chunkText(longSentences, 400, 80);
    expect(chunks.length).toBeGreaterThan(1);
    for (const c of chunks) {
      expect(c.length).toBeLessThanOrEqual(700); // 400 maxSize + 200 overlap headroom
    }
  });

  it('prepends overlap from previous chunk across paragraphs', () => {
    const para1 = 'Lorem ipsum dolor sit amet aspirin synthesis discussion paragraph one.';
    const para2 = 'Salicylic acid reaction with acetic anhydride yields the target.';
    const chunks = chunkText(`${para1}\n\n${para2}`, 1200, 30);
    expect(chunks).toHaveLength(2);
    // Second chunk must start with some tail of the first paragraph.
    const firstTail = para1.slice(-30);
    expect(chunks[1].startsWith(firstTail)).toBe(true);
  });

  it('first chunk has no overlap prefix', () => {
    const para = 'Aspirin is used to reduce pain and inflammation in the body.';
    const chunks = chunkText(para);
    expect(chunks[0]).toBe(para);
  });

  it('falls back to word boundaries on run-on sentences', () => {
    const runon = 'token '.repeat(500).trim(); // ~3000 chars, no period
    const chunks = chunkText(runon, 400, 80);
    expect(chunks.length).toBeGreaterThan(1);
    for (const c of chunks) {
      expect(c.length).toBeLessThanOrEqual(700);
    }
  });

  it('emits a markdown table as a single chunk (Wave-2c B4)', () => {
    const md = [
      'Some intro prose explaining what follows.',
      '',
      '| yield | catalyst | solvent |',
      '|---|---|---|',
      '| 80%   | Pd/C     | EtOH    |',
      '| 60%   | Ni       | MeOH    |',
      '',
      'Closing prose paragraph.',
    ].join('\n');
    const chunks = chunkText(md);
    // exactly one chunk should contain the divider row — verifies the table
    // wasn't split into header / row / row across separate chunks.
    const tableChunks = chunks.filter((c) => /\|---/.test(c));
    expect(tableChunks).toHaveLength(1);
    expect(tableChunks[0]).toContain('80%');
    expect(tableChunks[0]).toContain('60%');
    expect(tableChunks[0]).toContain('catalyst');
  });

  it('still splits prose surrounding a table normally', () => {
    // Each prose paragraph must exceed the > 10 char filter in pushWithOverlap.
    const md = [
      'Paragraph A discusses the experimental setup at length.',
      '',
      '| x | y |',
      '|---|---|',
      '| 1 | 2 |',
      '',
      'Paragraph B discusses the observed outcomes at length.',
    ].join('\n');
    const chunks = chunkText(md);
    // prose A, table, prose B — table must be its own chunk; prose chunks
    // must NOT contain the table divider.
    expect(chunks.length).toBeGreaterThanOrEqual(2);
    const proseChunks = chunks.filter((c) => !/\|---/.test(c));
    expect(proseChunks.some((c) => c.includes('Paragraph A'))).toBe(true);
    expect(proseChunks.some((c) => c.includes('Paragraph B'))).toBe(true);
  });
});
