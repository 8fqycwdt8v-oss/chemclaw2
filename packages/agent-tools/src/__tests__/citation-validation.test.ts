import { describe, it, expect } from 'vitest';
import { validateCitations } from '../citation-validation';

describe('validateCitations', () => {
  it('accepts a body with all markers resolved', () => {
    const v = validateCitations(
      'See [1] and [2] for details.',
      [
        { citationId: '1', sourceType: 'doc', label: 'Paper A' },
        { citationId: '2', sourceType: 'doc', label: 'Paper B' },
      ],
    );
    expect(v.ok).toBe(true);
  });

  it('rejects dangling [N] markers', () => {
    const v = validateCitations(
      'See [1] and [2] for details.',
      [{ citationId: '1', sourceType: 'doc', label: 'Paper A' }],
    );
    expect(v.ok).toBe(false);
    if (!v.ok) expect(v.reason).toMatch(/\[2\]/);
  });

  it('rejects duplicate citationIds', () => {
    const v = validateCitations(
      'See [1] for details.',
      [
        { citationId: '1', sourceType: 'doc', label: 'a' },
        { citationId: '1', sourceType: 'doc', label: 'b' },
      ],
    );
    expect(v.ok).toBe(false);
    if (!v.ok) expect(v.reason).toMatch(/duplicate/);
  });

  it('accepts URL citations on allowed science domains', () => {
    const v = validateCitations(
      'See [1].',
      [{ citationId: '1', sourceType: 'url', sourceId: 'https://doi.org/10.1000/foo', label: 'paper' }],
    );
    expect(v.ok).toBe(true);
  });

  it('accepts subdomains of allowed science domains', () => {
    const v = validateCitations(
      'See [1].',
      [{ citationId: '1', sourceType: 'url', sourceId: 'https://pubchem.ncbi.nlm.nih.gov/compound/2244', label: 'aspirin' }],
    );
    expect(v.ok).toBe(true);
  });

  it('rejects URL citations on disallowed domains', () => {
    const v = validateCitations(
      'See [1].',
      [{ citationId: '1', sourceType: 'url', sourceId: 'https://attacker.example.com/fake', label: 'paper' }],
    );
    expect(v.ok).toBe(false);
    if (!v.ok) expect(v.reason).toMatch(/allowed science-domain/);
  });

  it('accepts non-numeric markers like [a]', () => {
    const v = validateCitations(
      'See [a] for context.',
      [{ citationId: 'a', sourceType: 'doc', label: 'Paper A' }],
    );
    expect(v.ok).toBe(true);
  });

  it('ignores [text with spaces] which are not citation markers', () => {
    const v = validateCitations(
      'See [text with spaces] but no real citation.',
      [],
    );
    expect(v.ok).toBe(true);
  });

  it('rejects URL citations with non-http schemes', () => {
    const v = validateCitations(
      'See [1].',
      [{ citationId: '1', sourceType: 'url', sourceId: 'javascript:alert(1)', label: 'oops' }],
    );
    expect(v.ok).toBe(false);
  });
});
