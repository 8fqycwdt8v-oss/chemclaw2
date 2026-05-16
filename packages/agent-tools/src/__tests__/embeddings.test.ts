import { describe, it, expect } from 'vitest';
import {
  EMBED_MODEL,
  EMBED_DIM,
  EMBED_CHAR_LIMIT,
  prepareEmbeddingInputs,
  stripMarkdownForEmbedding,
} from '../embeddings';

describe('embedding constants', () => {
  it('locks model and dim to vector(1536) column expectations', () => {
    expect(EMBED_MODEL).toBe('text-embedding-3-small');
    expect(EMBED_DIM).toBe(1536);
  });
});

describe('prepareEmbeddingInputs', () => {
  it('returns inputs in the same order and length', () => {
    const out = prepareEmbeddingInputs(['a long enough first input', 'second input here']);
    expect(out).toHaveLength(2);
    expect(out[0]).toContain('first');
    expect(out[1]).toContain('second');
  });

  it('throws on empty/whitespace inputs rather than silently dropping them', () => {
    expect(() => prepareEmbeddingInputs(['ok input', '   '])).toThrow(/empty/);
    expect(() => prepareEmbeddingInputs(['ok input', ''])).toThrow(/empty/);
  });

  it('truncates over-long inputs to the char limit', () => {
    const big = 'x'.repeat(EMBED_CHAR_LIMIT + 500);
    const [out] = prepareEmbeddingInputs([big]);
    expect(out.length).toBe(EMBED_CHAR_LIMIT);
  });
});

describe('stripMarkdownForEmbedding', () => {
  it('strips header markers but keeps the heading text', () => {
    expect(stripMarkdownForEmbedding('## Aspirin Synthesis')).toBe('Aspirin Synthesis');
    expect(stripMarkdownForEmbedding('# Title\n\nbody')).toBe('Title\n\nbody');
  });

  it('strips paired bold markers', () => {
    expect(stripMarkdownForEmbedding('use **acetic anhydride** here')).toBe('use acetic anhydride here');
    expect(stripMarkdownForEmbedding('__caution__ scheme')).toBe('caution scheme');
  });

  it('strips paired inline backticks', () => {
    expect(stripMarkdownForEmbedding('Mol `CCO` is ethanol')).toBe('Mol CCO is ethanol');
  });

  it('leaves SMILES wildcard asterisks alone', () => {
    // A single `*` outside of `**...**` should not be touched.
    expect(stripMarkdownForEmbedding('Use CCN(C)C[*]CC pattern')).toBe('Use CCN(C)C[*]CC pattern');
  });

  it('strips bullet and numbered-list markers at line start', () => {
    expect(stripMarkdownForEmbedding('- step one\n- step two')).toBe('step one\nstep two');
    expect(stripMarkdownForEmbedding('1. first\n2. second')).toBe('first\nsecond');
  });

  it('strips HTML tags', () => {
    expect(stripMarkdownForEmbedding('<b>bold</b> text')).toBe('bold text');
  });

  it('strips fenced code block fences', () => {
    const md = '```python\nimport rdkit\n```';
    expect(stripMarkdownForEmbedding(md)).toContain('import rdkit');
    expect(stripMarkdownForEmbedding(md)).not.toContain('```');
  });

  it('preserves [N] citation markers (they carry semantic content)', () => {
    expect(stripMarkdownForEmbedding('see [1] and [2]')).toBe('see [1] and [2]');
  });
});
