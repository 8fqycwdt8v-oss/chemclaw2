import { describe, it, expect } from 'vitest';
import { isValidTiptapDoc } from '../tiptap';

describe('isValidTiptapDoc', () => {
  it('accepts the minimal Tiptap doc shape', () => {
    expect(isValidTiptapDoc({ type: 'doc', content: [] })).toBe(true);
  });

  it('accepts a doc with paragraph content', () => {
    expect(isValidTiptapDoc({
      type: 'doc',
      content: [{ type: 'paragraph', content: [{ type: 'text', text: 'hello' }] }],
    })).toBe(true);
  });

  it('rejects null and undefined', () => {
    expect(isValidTiptapDoc(null)).toBe(false);
    expect(isValidTiptapDoc(undefined)).toBe(false);
  });

  it('rejects non-object primitives', () => {
    expect(isValidTiptapDoc('doc')).toBe(false);
    expect(isValidTiptapDoc(42)).toBe(false);
    expect(isValidTiptapDoc(true)).toBe(false);
  });

  it('rejects wrong top-level type', () => {
    expect(isValidTiptapDoc({ type: 'paragraph', content: [] })).toBe(false);
    expect(isValidTiptapDoc({ content: [] })).toBe(false);
  });

  it('rejects missing or non-array content', () => {
    expect(isValidTiptapDoc({ type: 'doc' })).toBe(false);
    expect(isValidTiptapDoc({ type: 'doc', content: null })).toBe(false);
    expect(isValidTiptapDoc({ type: 'doc', content: 'paragraph' })).toBe(false);
    expect(isValidTiptapDoc({ type: 'doc', content: {} })).toBe(false);
  });

  it('does not deep-validate node shapes (Tiptap renderer drops unknowns)', () => {
    expect(isValidTiptapDoc({
      type: 'doc',
      content: [{ wholly: 'unknown', shape: true }],
    })).toBe(true);
  });
});
