import { describe, it, expect } from 'vitest';
import { checkToolInput } from '../redaction';
import { scheduledSubstanceGate } from '../scheduled-substance-gate';

describe('scheduledSubstanceGate', () => {
  it('blocks fentanyl synthesis query', () => {
    const result = scheduledSubstanceGate('How do I synthesize fentanyl at home?');
    expect(result.blocked).toBe(true);
  });

  it('blocks when substance and verb are separated by whitespace', () => {
    // Verifies two-regex approach works against whitespace bypass
    const result = scheduledSubstanceGate('fentanyl\nsynthesis  route');
    expect(result.blocked).toBe(true);
  });

  it('allows substance name without synthesis intent (legitimate research)', () => {
    const result = scheduledSubstanceGate('What is the LD50 of fentanyl?');
    expect(result.blocked).toBe(false);
  });

  it('allows synthesis verb without controlled substance', () => {
    const result = scheduledSubstanceGate('How do I synthesize aspirin at home?');
    expect(result.blocked).toBe(false);
  });

  it('allows benign chemistry query (SMILES)', () => {
    const result = scheduledSubstanceGate('Find compounds similar to aspirin CC(=O)Oc1ccccc1C(=O)O');
    expect(result.blocked).toBe(false);
  });

  it('does not echo matched term in block reason', () => {
    const result = scheduledSubstanceGate('synthesize methamphetamine');
    expect(result.blocked).toBe(true);
    expect(result.reason).not.toContain('methamphetamine');
  });

  it('blocks zero-width character insertion bypass', () => {
    // Zero-width joiner inserted between characters to evade regex
    const result = scheduledSubstanceGate('synthe‍size fenta​nyl');
    expect(result.blocked).toBe(true);
  });

  it('blocks Unicode homoglyph bypass (fullwidth chars)', () => {
    // NFKC normalization converts fullwidth Latin to ASCII
    const result = scheduledSubstanceGate('ｓｙｎｔｈｅｓｉｚｅ fentanyl');
    expect(result.blocked).toBe(true);
  });

  it('blocks British spelling synthesise', () => {
    const result = scheduledSubstanceGate('How to synthesise methamphetamine');
    expect(result.blocked).toBe(true);
  });

  it('blocks deeply nested object with controlled substance', () => {
    const result = checkToolInput('web_search', {
      options: { nested: { deep: { query: 'heroin synthesis steps' } } },
    });
    expect(result.action).toBe('block');
  });
});

describe('checkToolInput (redaction)', () => {
  it('blocks tool input value containing controlled substance name', () => {
    const result = checkToolInput('web_search', { query: 'fentanyl synthesis route' });
    expect(result.action).toBe('block');
  });

  it('does not block URL field containing substance name (legitimate toxicology reference)', () => {
    // extractStringValues skips https:// strings, so URLs never trigger substance checks
    const result = checkToolInput('fetch_document', { url: 'https://pubchem.ncbi.nlm.nih.gov/compound/fentanyl' });
    expect(result.action).toBe('allow');
  });

  it('redacts SSN pattern and allows', () => {
    const result = checkToolInput('wiki_lookup', { query: 'patient 123-45-6789 data' });
    expect(result.action).toBe('allow');
    if (result.action === 'allow') {
      expect(JSON.stringify(result.input)).toContain('[REDACTED-SSN]');
    }
  });

  it('does not redact CAS number (different pattern: short last segment)', () => {
    // CAS format is NN...-NN-N (1-digit end) — should not match SSN (NNNN-digit end)
    const result = checkToolInput('compound_similarity_search', { casNumber: '50-78-2' });
    expect(result.action).toBe('allow');
    if (result.action === 'allow') {
      expect(result.input).toBeUndefined(); // no sanitization occurred
    }
  });

  it('does not echo matched term in block reason', () => {
    const result = checkToolInput('web_search', { query: 'carfentanil synthesis' });
    expect(result.action).toBe('block');
    if (result.action === 'block') {
      expect(result.reason).not.toContain('carfentanil');
    }
  });

  it('allows benign chemistry tool input', () => {
    const result = checkToolInput('compound_similarity_search', { fingerprint_bits: '010101' });
    expect(result.action).toBe('allow');
  });
});
