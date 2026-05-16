import { describe, it, expect } from 'vitest';
import { validateCitations } from '../citation-validation';

describe('validateCitations', () => {
  it('accepts a body with all numeric markers resolved', () => {
    const v = validateCitations(
      'See [1] and [2] for details.',
      [
        { citationId: '1', sourceType: 'doc', label: 'Paper A' },
        { citationId: '2', sourceType: 'doc', label: 'Paper B' },
      ],
    );
    expect(v.ok).toBe(true);
  });

  it('rejects dangling numeric markers', () => {
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

  it('ignores SMILES atom brackets (R2 regression)', () => {
    // Common SMILES content the agent might write about — must not be treated
    // as citation markers, even though they sit inside square brackets.
    const body = 'Indole has [nH] nitrogen; methyl is [CH3]; chloride is [Cl-].';
    const v = validateCitations(body, []);
    expect(v.ok).toBe(true);
  });

  it('ignores markdown links (R3 regression)', () => {
    // `[paper](url)` is conventional markdown; the [paper] half must not be
    // treated as a citation reference.
    const body = 'See [paper](https://doi.org/10.1000/foo) for context.';
    const v = validateCitations(body, []);
    expect(v.ok).toBe(true);
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

  it('rejects URL citations on disallowed domains regardless of sourceType (R4)', () => {
    // R4 was: sourceType='doi' (or anything outside URL_LIKE_TYPES) bypassed
    // the allowlist check. Now any http(s) URL in sourceId is checked.
    for (const sourceType of ['url', 'doi', 'paper', 'pdf', 'random']) {
      const v = validateCitations(
        'See [1].',
        [{ citationId: '1', sourceType, sourceId: 'https://attacker.example.com/fake', label: 'paper' }],
      );
      expect(v.ok).toBe(false);
      if (!v.ok) expect(v.reason).toMatch(/allowed science-domain/);
    }
  });

  it('checks the label field too — URLs hidden there are still validated', () => {
    const v = validateCitations(
      'See [1].',
      [{ citationId: '1', sourceType: 'doc', label: 'https://attacker.example.com/x' }],
    );
    expect(v.ok).toBe(false);
  });

  it('rejects URL citations with non-http schemes', () => {
    const v = validateCitations(
      'See [1].',
      [{ citationId: '1', sourceType: 'url', sourceId: 'http://evil.example/x', label: 'oops' }],
    );
    expect(v.ok).toBe(false);
  });

  it('ignores non-numeric markers like [a] (intentional trade-off)', () => {
    // Tightened to digits-only after the SMILES-bracket false positives.
    // Alpha markers are no longer enforced; agents lose the consistency check
    // for those but no longer get blocked by chemistry content.
    const v = validateCitations('See [a] for context.', []);
    expect(v.ok).toBe(true);
  });
});
