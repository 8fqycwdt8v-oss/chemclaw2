import { describe, it, expect } from 'vitest';
import { extractMarkdownTables } from '../queries/wiki-tables';

describe('extractMarkdownTables', () => {
  it('returns [] when no markdown table is present', () => {
    expect(extractMarkdownTables('Just some prose, no tables.\n\nMore prose.')).toEqual([]);
  });

  it('extracts a single basic table with headers and rows', () => {
    const md = [
      '| yield | catalyst | temp |',
      '|---|---|---|',
      '| 75% | Pd/C | 80C |',
      '| 60% | Ni | 60C |',
    ].join('\n');
    const tables = extractMarkdownTables(md);
    expect(tables).toHaveLength(1);
    expect(tables[0].headers).toEqual(['yield', 'catalyst', 'temp']);
    expect(tables[0].rows).toHaveLength(2);
    expect(tables[0].rows[0]).toEqual({ yield: '75%', catalyst: 'Pd/C', temp: '80C' });
  });

  it('handles tables with outer pipes and trailing whitespace', () => {
    const md = [
      '|a|b|',
      '|---|---|',
      '|1|2|',
    ].join('\n');
    const tables = extractMarkdownTables(md);
    expect(tables[0].headers).toEqual(['a', 'b']);
    expect(tables[0].rows[0]).toEqual({ a: '1', b: '2' });
  });

  it('ignores stray pipes in prose (no divider row follows)', () => {
    const md = 'This sentence has a | pipe in it.\nBut no table.';
    expect(extractMarkdownTables(md)).toEqual([]);
  });

  it('records the anchor as the nearest preceding heading', () => {
    const md = [
      '## Yield Optimization',
      '',
      '| yield | catalyst |',
      '|---|---|',
      '| 80% | Pd |',
    ].join('\n');
    const tables = extractMarkdownTables(md);
    expect(tables[0].anchor).toBe('Yield Optimization');
  });

  it('extracts multiple tables and increments position', () => {
    const md = [
      '| a | b |',
      '|---|---|',
      '| 1 | 2 |',
      '',
      'Some prose between tables.',
      '',
      '| x | y |',
      '|---|---|',
      '| 9 | 8 |',
    ].join('\n');
    const tables = extractMarkdownTables(md);
    expect(tables).toHaveLength(2);
    expect(tables[0].position).toBe(0);
    expect(tables[1].position).toBe(1);
  });

  it('truncates row cells to the header count when row has too many', () => {
    const md = [
      '| a | b |',
      '|---|---|',
      '| 1 | 2 | 3 |',
    ].join('\n');
    const tables = extractMarkdownTables(md);
    expect(tables[0].rows[0]).toEqual({ a: '1', b: '2' });
  });

  it('skips a header without any data rows', () => {
    const md = [
      '| a | b |',
      '|---|---|',
      '',
      'next prose',
    ].join('\n');
    expect(extractMarkdownTables(md)).toEqual([]);
  });
});
