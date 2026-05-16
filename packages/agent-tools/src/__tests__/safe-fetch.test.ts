import { describe, it, expect } from 'vitest';
import { safeFetch } from '../safe-fetch';

const ALLOWED = ['pubchem.ncbi.nlm.nih.gov'];

describe('safeFetch SSRF guards', () => {
  it('rejects a domain outside the allowlist before any DNS lookup', async () => {
    await expect(safeFetch('https://attacker.example.com/x', ALLOWED))
      .rejects.toThrow(/Domain not allowed/);
  });

  it('accepts www. prefix as equivalent to bare domain', async () => {
    // Won't actually hit the network — just verify that the allowlist match
    // gets past the initial check (DNS lookup of www. will then succeed or
    // fail on its own merits, which we don't care about here).
    let reachedDns = false;
    try {
      await safeFetch('https://www.pubchem.ncbi.nlm.nih.gov/dummy', ALLOWED);
    } catch (e) {
      reachedDns = !/Domain not allowed/.test((e as Error).message);
    }
    expect(reachedDns).toBe(true);
  });

  it('rejects literal IP for a loopback address', async () => {
    await expect(safeFetch('http://127.0.0.1/foo', ['127.0.0.1']))
      .rejects.toThrow(/non-public address/);
  });

  it('rejects an RFC1918 private range', async () => {
    await expect(safeFetch('http://10.0.0.1/foo', ['10.0.0.1']))
      .rejects.toThrow(/non-public address/);
  });

  it('rejects link-local 169.254.169.254 (cloud metadata)', async () => {
    await expect(safeFetch('http://169.254.169.254/latest/meta-data/', ['169.254.169.254']))
      .rejects.toThrow(/non-public address/);
  });

  it('rejects IPv6 loopback ::1', async () => {
    // URL.hostname returns "[::1]" with brackets; the allowlist comparator
    // doesn't normalize that to "::1", so the rejection fires via the
    // domain-not-allowed path rather than the DNS path. Either is a safe stop.
    await expect(safeFetch('http://[::1]/foo', ['::1']))
      .rejects.toThrow(/not allowed|non-public address/);
  });

  it('rejects allowed parent domain attack — foo.com.attacker.com', async () => {
    await expect(safeFetch('https://pubchem.ncbi.nlm.nih.gov.attacker.com/x', ALLOWED))
      .rejects.toThrow(/Domain not allowed/);
  });

  it('rejects substring host that does not start at a label boundary', async () => {
    // not-pubchem.ncbi.nlm.nih.gov should not match pubchem.ncbi.nlm.nih.gov
    await expect(safeFetch('https://evilpubchem.ncbi.nlm.nih.gov/x', ['pubchem.ncbi.nlm.nih.gov']))
      .rejects.toThrow(/Domain not allowed/);
  });
});
