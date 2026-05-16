import Link from 'next/link';
import { redirect } from 'next/navigation';
import { listPendingProposedEdits } from '@chemclaw2/db';
import { getAdminContext } from '@/lib/auth';

/**
 * Wave-3d: reviewer UI for the propose-edit/apply protocol shipped in Wave 3c.
 *
 * Server-component list of pending wiki edit proposals; per-proposal apply /
 * reject UI lives at /admin/wiki/queue/[id]. Admin-only via the shared
 * `getAdminContext` helper.
 */
export default async function WikiReviewQueuePage() {
  const { userId, isAdmin } = await getAdminContext();
  if (!userId) redirect('/sign-in');
  if (!isAdmin) {
    return (
      <div className="text-sm text-slate-600">
        Admin role required. Ask an admin to grant you the role in Clerk.
      </div>
    );
  }

  const pending = await listPendingProposedEdits();

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Wiki review queue</h1>
        <div className="text-xs text-slate-500">{pending.length} pending</div>
      </div>

      {pending.length === 0 ? (
        <div className="text-sm text-slate-500 border rounded p-4">
          No pending proposals. Agent-staged wiki edits land here for human review.
        </div>
      ) : (
        <ul className="divide-y border rounded">
          {pending.map((p) => (
            <li key={p.id} className="p-3 hover:bg-slate-50">
              <div className="flex items-baseline justify-between gap-3">
                <div className="min-w-0 flex-1">
                  <Link href={`/admin/wiki/queue/${p.id}`} className="text-sm font-medium text-slate-900 hover:underline">
                    {p.title}
                  </Link>
                  <div className="text-xs text-slate-500 mt-0.5">
                    slug: <span className="font-mono">{p.slug}</span>
                    {' · proposed by '}<span className="font-mono">{p.proposedBy}</span>
                    {' · '}{new Date(p.createdAt).toLocaleString()}
                  </div>
                  {p.rationale && (
                    <div className="text-xs text-slate-700 mt-1 italic">
                      {p.rationale.length > 200 ? p.rationale.slice(0, 200) + '…' : p.rationale}
                    </div>
                  )}
                </div>
                <Link
                  href={`/admin/wiki/queue/${p.id}`}
                  className="text-xs px-2 py-1 border rounded text-slate-700 hover:bg-slate-100 shrink-0"
                >
                  Review
                </Link>
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
