'use client';
import { useState, useCallback } from 'react';
import Link from 'next/link';
import type { JSONContent } from '@tiptap/react';
import { WikiEditor } from './WikiEditor';

type Citation = { citationId: string; sourceType: string; sourceId?: string; label: string };
type RevisionRef = { version: number; updatedAt: string; updatedBy: string };

type Props = {
  slug: string;
  title: string;
  content: Record<string, unknown>;
  contentText: string;
  version: number;
  updatedAt: string;
  updatedBy: string;
  citations: Citation[];
  revisions: RevisionRef[];
};

export function WikiPageView(p: Props) {
  const [editing, setEditing] = useState(false);
  const [showHistory, setShowHistory] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);

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

  return (
    <article className="space-y-4">
      <header className="space-y-1">
        <div className="flex items-baseline justify-between gap-4">
          <h1 className="text-2xl font-semibold">{p.title}</h1>
          <div className="flex gap-2 text-sm">
            <button
              onClick={() => setShowHistory((v) => !v)}
              className="text-slate-700 hover:underline"
            >
              History ({p.revisions.length})
            </button>
            {!editing && (
              <button onClick={() => setEditing(true)} className="text-blue-600 hover:underline">
                Edit
              </button>
            )}
          </div>
        </div>
        <div className="text-xs text-slate-500">
          v{p.version} · updated {new Date(p.updatedAt).toLocaleString()} · by {p.updatedBy}
        </div>
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

      {error && (
        <div className="text-red-700 text-sm bg-red-50 p-2 rounded">Error: {error}</div>
      )}

      <WikiEditor
        initialContent={p.content as JSONContent}
        onSave={onSave}
        readOnly={!editing}
      />
      {editing && (
        <button
          onClick={() => setEditing(false)}
          className="text-sm text-slate-600 hover:underline"
          disabled={saving}
        >
          Cancel edit
        </button>
      )}

      {p.citations.length > 0 && (
        <section className="border-t pt-4 mt-6">
          <h2 className="text-sm font-medium mb-2">Citations</h2>
          <ol className="text-sm space-y-1 list-decimal list-inside text-slate-700">
            {p.citations.map((c) => (
              <li key={c.citationId}>
                <span className="font-mono text-xs text-slate-500">[{c.citationId}]</span>{' '}
                {c.sourceId && c.sourceType === 'url' ? (
                  <a
                    href={c.sourceId}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="text-blue-600 hover:underline"
                  >
                    {c.label}
                  </a>
                ) : (
                  <>
                    {c.label} <span className="text-slate-400 text-xs">({c.sourceType})</span>
                  </>
                )}
              </li>
            ))}
          </ol>
        </section>
      )}

      <Link href="/wiki" className="text-sm text-slate-600 hover:underline inline-block">
        ← Back to wiki
      </Link>
    </article>
  );
}
