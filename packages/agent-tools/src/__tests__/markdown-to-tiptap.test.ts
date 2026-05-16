import { describe, it, expect } from 'vitest';
import { markdownToTiptap } from '../markdown-to-tiptap';

describe('markdownToTiptap', () => {
  it('emits a doc with at least one block for empty input', () => {
    const doc = markdownToTiptap('');
    expect(doc.type).toBe('doc');
    expect(doc.content.length).toBeGreaterThan(0);
  });

  it('converts a single paragraph', () => {
    const doc = markdownToTiptap('Aspirin is a salicylate.');
    expect(doc.content).toHaveLength(1);
    expect(doc.content[0].type).toBe('paragraph');
  });

  it('converts headers to heading nodes (h1-h3, deeper collapses to h3)', () => {
    const doc = markdownToTiptap('# H1\n\n## H2\n\n### H3\n\n#### H4');
    expect(doc.content).toHaveLength(4);
    const levels = doc.content
      .filter((b): b is Extract<typeof b, { type: 'heading' }> => b.type === 'heading')
      .map((h) => h.attrs.level);
    expect(levels).toEqual([1, 2, 3, 3]);
  });

  it('converts bullet lists', () => {
    const doc = markdownToTiptap('- alpha\n- beta\n- gamma');
    expect(doc.content).toHaveLength(1);
    expect(doc.content[0].type).toBe('bulletList');
    const list = doc.content[0] as Extract<typeof doc.content[0], { type: 'bulletList' }>;
    expect(list.content).toHaveLength(3);
  });

  it('converts ordered lists', () => {
    const doc = markdownToTiptap('1. first\n2. second');
    expect(doc.content).toHaveLength(1);
    expect(doc.content[0].type).toBe('orderedList');
  });

  it('parses bold inline marks', () => {
    const doc = markdownToTiptap('Use **acetic anhydride** here.');
    const para = doc.content[0] as Extract<typeof doc.content[0], { type: 'paragraph' }>;
    const boldNode = para.content?.find((n) => n.marks?.some((m) => m.type === 'bold'));
    expect(boldNode?.text).toBe('acetic anhydride');
  });

  it('parses inline code marks', () => {
    const doc = markdownToTiptap('SMILES `CCO` is ethanol.');
    const para = doc.content[0] as Extract<typeof doc.content[0], { type: 'paragraph' }>;
    const codeNode = para.content?.find((n) => n.marks?.some((m) => m.type === 'code'));
    expect(codeNode?.text).toBe('CCO');
  });

  it('does not parse lone asterisks in SMILES brackets as marks', () => {
    const doc = markdownToTiptap('Pattern is CCN(C)C[*]CC throughout.');
    const para = doc.content[0] as Extract<typeof doc.content[0], { type: 'paragraph' }>;
    const text = (para.content ?? []).map((n) => n.text).join('');
    expect(text).toBe('Pattern is CCN(C)C[*]CC throughout.');
  });

  it('does not parse double-wildcard SMILES as italic (R8 regression)', () => {
    // CC*CC*CCO — two wildcard atoms — must not become CC<italic>CC</italic>CCO.
    const doc = markdownToTiptap('SMILES CC*CC*CCO has two wildcards.');
    const para = doc.content[0] as Extract<typeof doc.content[0], { type: 'paragraph' }>;
    const text = (para.content ?? []).map((n) => n.text).join('');
    expect(text).toBe('SMILES CC*CC*CCO has two wildcards.');
    const italic = para.content?.find((n) => n.marks?.some((m) => (m as { type: string }).type === 'italic'));
    expect(italic).toBeUndefined();
  });

  it('preserves [N] citation markers verbatim', () => {
    const doc = markdownToTiptap('See [1] and [12] for context.');
    const para = doc.content[0] as Extract<typeof doc.content[0], { type: 'paragraph' }>;
    const text = (para.content ?? []).map((n) => n.text).join('');
    expect(text).toBe('See [1] and [12] for context.');
  });

  it('converts blockquotes', () => {
    const doc = markdownToTiptap('> Important note about the reaction.');
    expect(doc.content[0].type).toBe('blockquote');
  });

  it('converts fenced code blocks', () => {
    const doc = markdownToTiptap('```python\nimport rdkit\nrdkit.run()\n```');
    expect(doc.content[0].type).toBe('codeBlock');
  });
});
