'use client';
import { useState, useCallback, useEffect } from 'react';
import Link from 'next/link';
import type { JSONContent } from '@tiptap/react';
import { WikiEditor } from './WikiEditor';

type Citation = { citationId: string; sourceType: string; sourceId?: string; label: string; disputed?: boolean };
type RevisionRef = { version: number; updatedAt: string; updatedBy: string };

type Props = {
  slug: string;
  title: string;
  content: unknown;
  contentText: string;
  version: number;
  updatedAt: string;
  updatedBy: string;
  needsReview: boolean;
  archived: boolean;
  maturity: string;
  project: string | null;
  subscribed: boolean;
  citations: Citation[];
  revisions: RevisionRef[];
};

const MATURITIES = ['exploratory', 'validated', 'authoritative'] as const;

export function WikiPageView(p: Props) {
  const [editing, setEditing] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [needsReview, setNeedsReview] = useState(p.needsReview);
  const [archived, setArchived] = useState(p.archived);
  const [maturity, setMaturity] = useState(p.maturity);
  const [subscribed, setSubscribed] = useState(p.subscribed);
  const [citations, setCitations] = useState(p.citations);

  // Scroll to citation anchor when arriving with ?cid=... from a chat link.
  useEffect(() => {
    const cid = new URLSearchParams(location.search).get('cid');
    if (!cid) return;
    const el = document.querySelector(`[data-cid="${cid}"]`);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'center' });
  }, []);

  const onSave = useCallback(
    async (content: JSONContent, text: string) => {
      setSaving(true);
      setError(null);
      try {
        const res = await fetch(`/api/wiki/${p.slug}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ content, contentText: text }),
        });
        if (!res.ok) {
          const body = (await res.json().catch(() => null)) as { error?: string } | null;
          throw new Error(body?.error ?? `Save failed (${res.status})`);
        }
        location.reload();
      } catch (err) {
        setError((err as Error).message);
        throw err;
      } finally {
        setSaving(false);
      }
    },
    [p.slug],
  );

  async function patchMetadata(patch: Record<string, unknown>) {
    try {
      const res = await fetch(`/api/wiki/${p.slug}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => null)) as { error?: string } | null;
        throw new Error(body?.error ?? `Update failed (${res.status})`);
      }
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function toggleSubscribed() {
    const next = !subscribed;
    setSubscribed(next);
    try {
      const res = await fetch(`/api/wiki/${p.slug}/subscribe`, { method: next ? 'POST' : 'DELETE' });
      if (!res.ok) throw new Error(`Subscription update failed (${res.status})`);
    } catch (err) {
      setSubscribed(!next);
      setError((err as Error).message);
    }
  }

  async function toggleDispute(citationId: string, disputed: boolean) {
    setCitations((cs) => cs.map((c) => (c.citationId === citationId ? { ...c, disputed } : c)));
    try {
      const res = await fetch(`/api/wiki/${p.slug}/citations/${encodeURIComponent(citationId)}/dispute`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ disputed }),
      });
      if (!res.ok) throw new Error('Dispute update failed');
    } catch (err) {
      setCitations((cs) => cs.map((c) => (c.citationId === citationId ? { ...c, disputed: !disputed } : c)));
      setError((err as Error).message);
    }
  }

  return (
    <article className="space-y-4">
      <header className="space-y-2">
        <div className="flex items-baseline justify-between gap-4">
          <h1 className="text-2xl font-semibold">{p.title}</h1>
          <div className="flex gap-3 text-sm">
            <button onClick={toggleSubscribed} className={subscribed ? 'text-blue-600' : 'text-slate-700'}>
              {subscribed ? '★ Watching' : '☆ Watch'}
            </button>
            <button onClick={() => setShowHistory((v) => !v)} className="text-slate-700 hover:underline">
              History ({p.revisions.length})
            </button>
            {!editing && (
              <button onClick={() => setEditing(true)} className="text-blue-600 hover:underline">Edit</button>
            )}
          </div>
        </div>
        <div className="flex flex-wrap items-center gap-2 text-xs">
          <MaturityBadge value={maturity} />
          {needsReview && <span className="px-2 py-0.5 rounded bg-amber-100 text-amber-800">needs review</span>}
          {archived && <span className="px-2 py-0.5 rounded bg-slate-200 text-slate-700">archived</span>}
          {p.project && (
            <Link href={`/wiki?project=${encodeURIComponent(p.project)}`}
              className="px-2 py-0.5 rounded bg-purple-100 text-purple-800">
              {p.project}
            </Link>
          )}
          <span className="text-slate-500 ml-2">
            v{p.version} · {new Date(p.updatedAt).toLocaleString()} · {p.updatedBy}
          </span>
        </div>
        {editing && (
          <div className="flex flex-wrap gap-2 text-xs border-t pt-2">
            <button onClick={() => { setNeedsReview(!needsReview); void patchMetadata({ needsReview: !needsReview }); }}
              className="px-2 py-1 border rounded">
              {needsReview ? 'Clear needs-review' : 'Flag needs-review'}
            </button>
            <button onClick={() => { setArchived(!archived); void patchMetadata({ archived: !archived }); }}
              className="px-2 py-1 border rounded">
              {archived ? 'Unarchive' : 'Archive'}
            </button>
            <select value={maturity}
              onChange={(e) => { setMaturity(e.target.value); void patchMetadata({ maturity: e.target.value }); }}
              className="px-2 py-1 border rounded">
              {MATURITIES.map((m) => <option key={m} value={m}>{m}</option>)}
            </select>
          </div>
        )}
      </header>

      {showHistory && (
        <div className="border rounded p-3 bg-slate-50 text-sm">
          <div className="font-medium mb-2">Revision history</div>
          {p.revisions.length === 0 ? (
            <div className="text-slate-500">No prior revisions.</div>
          ) : (
            <ul className="space-y-1">
              {p.revisions.map((r) => (
                <li key={r.version} className="text-slate-700">
                  v{r.version} · {new Date(r.updatedAt).toLocaleString()} · {r.updatedBy}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      {error && <div className="text-red-700 text-sm bg-red-50 p-2 rounded">Error: {error}</div>}

      <WikiEditor initialContent={p.content as JSONContent} onSave={onSave} readOnly={!editing} />
      {editing && (
        <button onClick={() => setEditing(false)} className="text-sm text-slate-600 hover:underline" disabled={saving}>
          Cancel edit
        </button>
      )}

      {citations.length > 0 && (
        <section className="border-t pt-4 mt-6">
          <h2 className="text-sm font-medium mb-2">Citations</h2>
          <ol className="text-sm space-y-1 list-decimal list-inside text-slate-700">
            {citations.map((c) => (
              <li key={c.citationId} data-cid={c.citationId}
                className={c.disputed ? 'line-through text-slate-400' : ''}>
                <span className="font-mono text-xs text-slate-500">[{c.citationId}]</span>{' '}
                {c.sourceId && c.sourceType === 'url' ? (
                  <a href={c.sourceId} target="_blank" rel="noreferrer noopener"
                    className="text-blue-600 hover:underline">{c.label}</a>
                ) : (
                  <>{c.label} <span className="text-slate-400 text-xs">({c.sourceType})</span></>
                )}
                {c.disputed && <span className="ml-2 text-xs text-red-600">[disputed]</span>}
                <button onClick={() => toggleDispute(c.citationId, !c.disputed)}
                  className="ml-2 text-xs text-slate-500 hover:text-slate-700 no-underline">
                  {c.disputed ? '(un-dispute)' : '(mark disputed)'}
                </button>
              </li>
            ))}
          </ol>
        </section>
      )}

      <Link href="/wiki" className="text-sm text-slate-600 hover:underline inline-block">← Back to wiki</Link>
    </article>
  );
}

function MaturityBadge({ value }: { value: string }) {
  const colorMap: Record<string, string> = {
    exploratory: 'bg-slate-100 text-slate-700',
    validated: 'bg-emerald-100 text-emerald-800',
    authoritative: 'bg-indigo-100 text-indigo-800',
  };
  return <span className={`px-2 py-0.5 rounded ${colorMap[value] ?? 'bg-slate-100'}`}>{value}</span>;
}
