import { UUID_RE } from '@/lib/validation';
import Link from 'next/link';
import { notFound, redirect } from 'next/navigation';
import { getProposedEdit, getWikiPage } from '@chemclaw2/db';
import { ReviewActions } from '@/components/admin/ReviewActions';
import { getAdminContext } from '@/lib/auth';

/**
 * Wave-3d proposed-edit detail page — side-by-side current vs. proposed
 * content_text. No prose-level diff library yet; reviewers eyeball the
 * difference. A token-level diff is a backlog'd polish if reviewers ask.
 */
export default async function ProposedEditDetailPage(
  { params }: { params: Promise<{ id: string }> },
) {
  const { userId, isAdmin } = await getAdminContext();
  if (!userId) redirect('/sign-in');
  if (!isAdmin) {
    return (
      <div className="text-sm text-slate-600">
        Admin role required.
      </div>
    );
  }

  const { id } = await params;
  if (!UUID_RE.test(id)) notFound();

  const proposal = await getProposedEdit(id);
  if (!proposal) notFound();

  const current = await getWikiPage(proposal.slug);
  const citationCount = Array.isArray(proposal.citations) ? proposal.citations.length : 0;

  return (
    <div className="space-y-4">
      <Link href="/admin/wiki/queue" className="text-xs text-slate-500 hover:text-slate-800">
        ← back to queue
      </Link>
      <div className="flex items-baseline justify-between">
        <h1 className="text-xl font-semibold">{proposal.title}</h1>
        <span className={
          'text-xs px-2 py-0.5 rounded ' +
          (proposal.status === 'pending'
            ? 'bg-amber-100 text-amber-900'
            : proposal.status === 'applied'
            ? 'bg-green-100 text-green-900'
            : proposal.status === 'rejected'
            ? 'bg-slate-200 text-slate-700'
            : 'bg-slate-100 text-slate-600')
        }>
          {proposal.status}
        </span>
      </div>
      <div className="text-xs text-slate-500">
        slug <span className="font-mono">{proposal.slug}</span>
        {' · proposed by '}<span className="font-mono">{proposal.proposedBy}</span>
        {' · '}{new Date(proposal.createdAt).toLocaleString()}
        {' · '}{citationCount} citation{citationCount === 1 ? '' : 's'}
        {proposal.previousId && (
          <>
            {' · supersedes '}
            <Link
              href={`/admin/wiki/queue/${proposal.previousId}`}
              className="font-mono text-blue-600 hover:underline"
            >
              {proposal.previousId.slice(0, 8)}…
            </Link>
          </>
        )}
      </div>
      {proposal.rationale && (
        <div className="text-sm border-l-4 border-amber-400 bg-amber-50 p-3">
          <div className="text-xs font-semibold text-amber-900 mb-1">Rationale</div>
          {proposal.rationale}
        </div>
      )}

      {proposal.status === 'pending' && <ReviewActions proposalId={proposal.id} />}

      {proposal.status === 'rejected' && proposal.reviewComment && (
        <div className="text-sm border-l-4 border-slate-400 bg-slate-50 p-3">
          <div className="text-xs font-semibold text-slate-700 mb-1">
            Rejected by {proposal.reviewedBy} on {proposal.reviewedAt && new Date(proposal.reviewedAt).toLocaleString()}
          </div>
          {proposal.reviewComment}
        </div>
      )}

      <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
        <section className="border rounded p-3 space-y-2">
          <div className="flex items-baseline justify-between">
            <h2 className="text-sm font-semibold text-slate-700">Current page</h2>
            {current ? (
              <Link href={`/wiki/${proposal.slug}`} className="text-xs text-blue-600 hover:underline">
                open ↗
              </Link>
            ) : (
              <span className="text-xs text-slate-400">does not exist yet</span>
            )}
          </div>
          <pre className="text-xs whitespace-pre-wrap font-mono leading-snug text-slate-800 max-h-[60vh] overflow-y-auto">
{current?.contentText ?? '(page does not exist — proposal would create it)'}
          </pre>
        </section>
        <section className="border rounded p-3 space-y-2 bg-amber-50/30">
          <h2 className="text-sm font-semibold text-amber-900">Proposed content</h2>
          <pre className="text-xs whitespace-pre-wrap font-mono leading-snug text-slate-800 max-h-[60vh] overflow-y-auto">
{proposal.contentText}
          </pre>
        </section>
      </div>
    </div>
  );
}
