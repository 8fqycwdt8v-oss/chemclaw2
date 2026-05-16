import { describe, it, expect } from 'vitest';
import { isValidSlug, RESERVED_SLUGS } from '../slug';

describe('isValidSlug', () => {
  it('accepts simple kebab-case', () => {
    expect(isValidSlug('parp-inhibitor-sar')).toBe(true);
    expect(isValidSlug('aspirin')).toBe(true);
    expect(isValidSlug('compound-123')).toBe(true);
  });

  it('rejects uppercase', () => {
    expect(isValidSlug('PARP')).toBe(false);
    expect(isValidSlug('parp-Inhibitor')).toBe(false);
  });

  it('rejects whitespace and punctuation', () => {
    expect(isValidSlug('hello world')).toBe(false);
    expect(isValidSlug('hello_world')).toBe(false);
    expect(isValidSlug('hello.world')).toBe(false);
  });

  it('rejects leading, trailing, and consecutive hyphens', () => {
    expect(isValidSlug('-foo')).toBe(false);
    expect(isValidSlug('foo-')).toBe(false);
    expect(isValidSlug('foo--bar')).toBe(false);
  });

  it('rejects strings longer than 200 chars', () => {
    expect(isValidSlug('a'.repeat(201))).toBe(false);
    expect(isValidSlug('a'.repeat(200))).toBe(true);
  });

  it('rejects every reserved slug', () => {
    for (const reserved of RESERVED_SLUGS) {
      expect(isValidSlug(reserved)).toBe(false);
    }
  });
});
