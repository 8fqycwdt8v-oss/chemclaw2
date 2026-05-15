import { describe, it, expect } from 'vitest';
import { checkToolInput } from '../redaction';
import { scheduledSubstanceGate } from '../scheduled-substance-gate';

describe('scheduledSubstanceGate', () => {
  it('blocks fentanyl synthesis query', () => {
    const result = scheduledSubstanceGate('How do I synthesize fentanyl at home?');
    expect(result.blocked).toBe(true);
  });

  it('allows benign chemistry query', () => {
    const result = scheduledSubstanceGate('Find compounds similar to aspirin CC(=O)Oc1ccccc1C(=O)O');
    expect(result.blocked).toBe(false);
  });
});

describe('checkToolInput (redaction)', () => {
  it('blocks tool input containing controlled substance term', () => {
    const result = checkToolInput('web_search', { query: 'fentanyl synthesis route' });
    expect(result.action).toBe('block');
  });

  it('redacts SSN pattern and allows', () => {
    const result = checkToolInput('wiki_lookup', { query: 'patient 123-45-6789 data' });
    expect(result.action).toBe('allow');
    if (result.action === 'allow') {
      expect(JSON.stringify(result.input)).toContain('[REDACTED-SSN]');
    }
  });

  it('allows benign chemistry tool input', () => {
    const result = checkToolInput('compound_similarity_search', { fingerprint_bits: '010101' });
    expect(result.action).toBe('allow');
  });
});
