import { describe, it, expect, vi, beforeEach } from 'vitest';

const { safeFetchMock } = vi.hoisted(() => ({ safeFetchMock: vi.fn() }));
vi.mock('../safe-fetch', () => ({ safeFetch: safeFetchMock }));

import { hazardLookupTool } from '../hazard-lookup';

function fakeResponse(opts: { ok: boolean; status?: number; json?: unknown; text?: string }) {
  return {
    ok: opts.ok,
    status: opts.status ?? (opts.ok ? 200 : 404),
    json: async () => opts.json,
    text: async () => opts.text ?? JSON.stringify(opts.json ?? {}),
  } as Response;
}

beforeEach(() => {
  safeFetchMock.mockReset();
});

describe('hazardLookupTool', () => {
  it('rejects empty input', async () => {
    const r = await hazardLookupTool.execute({ cas_or_smiles: '', kind: 'cas' });
    expect(r).toHaveProperty('error');
    expect(safeFetchMock).not.toHaveBeenCalled();
  });

  it('rejects input longer than 500 chars', async () => {
    const r = await hazardLookupTool.execute({ cas_or_smiles: 'a'.repeat(501), kind: 'smiles' });
    expect(r).toHaveProperty('error');
    expect(safeFetchMock).not.toHaveBeenCalled();
  });

  it('returns error when PubChem returns no CID for the identifier', async () => {
    safeFetchMock.mockResolvedValueOnce(fakeResponse({ ok: true, json: { IdentifierList: {} } }));
    const r = await hazardLookupTool.execute({ cas_or_smiles: '67-64-1', kind: 'cas' });
    expect(r).toHaveProperty('error');
    expect((r as { error: string }).error).toMatch(/No PubChem record/);
  });

  it('returns parsed GHS payload when PubChem responds', async () => {
    safeFetchMock
      .mockResolvedValueOnce(fakeResponse({ ok: true, json: { IdentifierList: { CID: [180] } } }))
      .mockResolvedValueOnce(fakeResponse({ ok: true, text: JSON.stringify({ pictograms: ['GHS02'] }) }));
    const r = (await hazardLookupTool.execute({ cas_or_smiles: '67-64-1', kind: 'cas' })) as
      Record<string, unknown>;
    expect(r.cid).toBe(180);
    expect((r.ghs_raw as Record<string, unknown>).pictograms).toEqual(['GHS02']);
  });

  it('returns a soft note (not an error) when PubChem has no GHS for the CID', async () => {
    safeFetchMock
      .mockResolvedValueOnce(fakeResponse({ ok: true, json: { IdentifierList: { CID: [42] } } }))
      .mockResolvedValueOnce(fakeResponse({ ok: false, status: 404 }));
    const r = (await hazardLookupTool.execute({ cas_or_smiles: 'CCO', kind: 'smiles' })) as
      Record<string, unknown>;
    expect(r.cid).toBe(42);
    expect(r.hazards).toBeNull();
    expect(r.note).toMatch(/no GHS/);
  });

  it('rejects oversized GHS responses', async () => {
    const huge = JSON.stringify({ pictograms: 'x'.repeat(250_000) });
    safeFetchMock
      .mockResolvedValueOnce(fakeResponse({ ok: true, json: { IdentifierList: { CID: [180] } } }))
      .mockResolvedValueOnce(fakeResponse({ ok: true, text: huge }));
    const r = await hazardLookupTool.execute({ cas_or_smiles: '67-64-1', kind: 'cas' });
    expect(r).toHaveProperty('error');
    expect((r as { error: string }).error).toMatch(/size limit/);
  });

  it('surfaces safeFetch errors (e.g. SSRF rejection) as { error }', async () => {
    safeFetchMock.mockRejectedValueOnce(new Error('Domain not allowed'));
    const r = await hazardLookupTool.execute({ cas_or_smiles: '67-64-1', kind: 'cas' });
    expect(r).toHaveProperty('error');
    expect((r as { error: string }).error).toMatch(/Domain not allowed/);
  });
});
