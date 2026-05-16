'use client';
import { useState } from 'react';
import { useRouter } from 'next/navigation';

/**
 * Wave-3d: inline approve / reject buttons for a single pending proposal.
 * Calls the admin POST routes shipped in Wave 3c (apply / reject). On
 * success, the queue page is re-fetched via router.refresh().
 */
export function ReviewActions({ proposalId }: { proposalId: string }) {
  const router = useRouter();
  const [busy, setBusy] = useState<null | 'apply' | 'reject'>(null);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<null | { kind: 'apply'; pageId: string } | { kind: 'reject' }>(null);

  const apply = async () => {
    const comment = window.prompt('Optional reviewer comment (or leave blank):') ?? '';
    setBusy('apply');
    setError(null);
    try {
      const res = await fetch(`/api/admin/wiki/proposed-edits/${proposalId}/apply`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(comment ? { comment } : {}),
      });
      const body = (await res.json().catch(() => null)) as { error?: string; page_id?: string } | null;
      if (!res.ok) throw new Error(body?.error ?? `Apply failed (${res.status})`);
      setDone({ kind: 'apply', pageId: body?.page_id ?? '' });
      router.refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  const reject = async () => {
    const comment = window.prompt('Reason for rejecting (required, 1-2000 chars):');
    if (!comment) return;
    if (comment.length > 2000) {
      setError('Comment too long (≤2000 chars).');
      return;
    }
    setBusy('reject');
    setError(null);
    try {
      const res = await fetch(`/api/admin/wiki/proposed-edits/${proposalId}/reject`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ comment }),
      });
      const body = (await res.json().catch(() => null)) as { error?: string } | null;
      if (!res.ok) throw new Error(body?.error ?? `Reject failed (${res.status})`);
      setDone({ kind: 'reject' });
      router.refresh();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  };

  if (done) {
    if (done.kind === 'apply') {
      return (
        <div className="text-sm text-green-700 bg-green-50 border border-green-200 rounded p-2">
          Applied. Page id: <span className="font-mono">{done.pageId}</span>
        </div>
      );
    }
    return (
      <div className="text-sm text-slate-700 bg-slate-50 border border-slate-200 rounded p-2">
        Rejected.
      </div>
    );
  }

  return (
    <div className="space-y-2">
      <div className="flex gap-2">
        <button
          type="button"
          onClick={() => void apply()}
          disabled={busy !== null}
          className="px-3 py-1.5 text-sm bg-green-600 text-white rounded disabled:opacity-50"
        >
          {busy === 'apply' ? 'Applying…' : 'Approve & apply'}
        </button>
        <button
          type="button"
          onClick={() => void reject()}
          disabled={busy !== null}
          className="px-3 py-1.5 text-sm border border-slate-300 text-slate-700 rounded hover:bg-slate-50 disabled:opacity-50"
        >
          {busy === 'reject' ? 'Rejecting…' : 'Reject'}
        </button>
      </div>
      {error && (
        <div className="text-xs text-red-700 bg-red-50 border border-red-200 rounded p-2">{error}</div>
      )}
    </div>
  );
}
