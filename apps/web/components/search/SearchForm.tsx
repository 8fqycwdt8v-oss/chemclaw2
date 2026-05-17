'use client';
import { useState } from 'react';

type Mode = 'compound' | 'reaction' | 'substructure';

type CompoundResult = {
  id: string;
  smiles: string;
  canonSmiles: string | null;
  name: string | null;
  casNumber: string | null;
  tanimoto: number;
};
type ReactionResult = {
  id: string;
  rxnSmiles: string;
  name: string | null;
  conditions: string | null;
  similarity: number;
};
type SubstructureResult = {
  id: string;
  smiles: string;
  canonSmiles: string | null;
  name: string | null;
  casNumber: string | null;
};

type Result =
  | { type: 'compound'; rows: CompoundResult[] }
  | { type: 'reaction'; rows: ReactionResult[] }
  | { type: 'substructure'; rows: SubstructureResult[] };

export function SearchForm() {
  const [mode, setMode] = useState<Mode>('compound');
  const [input, setInput] = useState('');
  const [createdAfter, setCreatedAfter] = useState('');
  const [hasCas, setHasCas] = useState(false);
  const [loading, setLoading] = useState(false);
  const [result, setResult] = useState<Result | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!input.trim()) return;
    setLoading(true);
    setError(null);
    setResult(null);
    try {
      if (mode === 'substructure') {
        const res = await fetch('/api/search', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ smarts: input.trim(), limit: 30 }),
        });
        if (!res.ok) throw new Error((await res.json()).error ?? `error ${res.status}`);
        const data = (await res.json()) as { type: 'substructure'; results: SubstructureResult[] };
        setResult({ type: 'substructure', rows: data.results });
        return;
      }

      // Compute fingerprint via the proxy
      const fpRes = await fetch('/api/fingerprint', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ kind: mode === 'reaction' ? 'reaction' : 'compound', smiles: input.trim() }),
      });
      if (!fpRes.ok) throw new Error((await fpRes.json()).error ?? `fingerprint failed (${fpRes.status})`);
      const fp = (await fpRes.json()) as { fingerprint_bits: string };

      const body: Record<string, unknown> =
        mode === 'reaction'
          ? { rxn_fingerprint_bits: fp.fingerprint_bits, limit: 30 }
          : {
              fingerprint_bits: fp.fingerprint_bits,
              limit: 30,
              filters: {
                ...(createdAfter ? { created_after: createdAfter } : {}),
                ...(hasCas ? { has_cas: true } : {}),
              },
            };
      const res = await fetch('/api/search', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!res.ok) throw new Error((await res.json()).error ?? `error ${res.status}`);
      const data = (await res.json()) as
        | { type: 'compound'; results: CompoundResult[] }
        | { type: 'reaction'; results: ReactionResult[] };
      setResult(
        data.type === 'compound'
          ? { type: 'compound', rows: data.results }
          : { type: 'reaction', rows: data.results },
      );
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="space-y-4">
      <form onSubmit={onSubmit} className="space-y-3 border rounded p-4">
        <div className="flex gap-4 text-sm">
          {(['compound', 'reaction', 'substructure'] as Mode[]).map((m) => (
            <label key={m} className="flex items-center gap-1 cursor-pointer">
              <input
                type="radio"
                name="mode"
                value={m}
                checked={mode === m}
                onChange={() => setMode(m)}
              />
              {m}
            </label>
          ))}
        </div>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          rows={3}
          placeholder={
            mode === 'reaction'
              ? 'Reaction SMILES (e.g. reactants>>products)'
              : mode === 'substructure'
                ? 'SMARTS pattern (e.g. c1ccccc1[NH2])'
                : 'SMILES (e.g. CC(=O)Oc1ccccc1C(=O)O)'
          }
          className="w-full border rounded p-2 text-sm font-mono"
        />
        {mode === 'compound' && (
          <div className="flex gap-3 text-xs text-slate-700">
            <label className="flex items-center gap-1">
              created after:
              <input
                type="date"
                value={createdAfter}
                onChange={(e) => setCreatedAfter(e.target.value)}
                className="border rounded px-1 py-0.5"
              />
            </label>
            <label className="flex items-center gap-1">
              <input type="checkbox" checked={hasCas} onChange={(e) => setHasCas(e.target.checked)} />
              has CAS number
            </label>
          </div>
        )}
        <button
          type="submit"
          disabled={loading || !input.trim()}
          className="bg-blue-600 text-white rounded px-4 py-2 text-sm disabled:opacity-50"
        >
          {loading ? 'Searching…' : 'Search'}
        </button>
      </form>
      {error && <div className="text-red-700 text-sm bg-red-50 p-2 rounded">Error: {error}</div>}
      {result && <ResultsTable result={result} />}
    </div>
  );
}

function ResultsTable({ result }: { result: Result }) {
  if (result.rows.length === 0) {
    return <div className="text-sm text-slate-500">No matches.</div>;
  }
  if (result.type === 'compound') {
    return (
      <table className="w-full text-sm border rounded">
        <thead className="bg-slate-50 text-left">
          <tr>
            <th className="p-2">Tanimoto</th>
            <th className="p-2">Name</th>
            <th className="p-2">CAS</th>
            <th className="p-2">SMILES</th>
          </tr>
        </thead>
        <tbody>
          {result.rows.map((r) => (
            <tr key={r.id} className="border-t">
              <td className="p-2 font-mono">{r.tanimoto.toFixed(3)}</td>
              <td className="p-2">{r.name ?? '—'}</td>
              <td className="p-2 font-mono text-xs">{r.casNumber ?? '—'}</td>
              <td className="p-2 font-mono text-xs">{r.canonSmiles ?? r.smiles}</td>
            </tr>
          ))}
        </tbody>
      </table>
    );
  }
  if (result.type === 'reaction') {
    return (
      <div className="space-y-2">
        <table className="w-full text-sm border rounded">
          <thead className="bg-slate-50 text-left">
            <tr>
              <th className="p-2">Similarity</th>
              <th className="p-2">Name</th>
              <th className="p-2">Conditions</th>
              <th className="p-2">Reaction SMILES</th>
            </tr>
          </thead>
          <tbody>
            {result.rows.map((r) => (
              <tr key={r.id} className="border-t">
                <td className="p-2 font-mono">{r.similarity.toFixed(3)}</td>
                <td className="p-2">{r.name ?? '—'}</td>
                <td className="p-2 text-xs">{r.conditions ?? '—'}</td>
                <td className="p-2 font-mono text-xs">{r.rxnSmiles}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    );
  }
  return (
    <table className="w-full text-sm border rounded">
      <thead className="bg-slate-50 text-left">
        <tr>
          <th className="p-2">Name</th>
          <th className="p-2">CAS</th>
          <th className="p-2">SMILES</th>
        </tr>
      </thead>
      <tbody>
        {result.rows.map((r) => (
          <tr key={r.id} className="border-t">
            <td className="p-2">{r.name ?? '—'}</td>
            <td className="p-2 font-mono text-xs">{r.casNumber ?? '—'}</td>
            <td className="p-2 font-mono text-xs">{r.canonSmiles ?? r.smiles}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
