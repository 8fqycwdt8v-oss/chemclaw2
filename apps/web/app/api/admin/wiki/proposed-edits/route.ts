import { NextResponse } from 'next/server';
import { listPendingProposedEdits } from '@chemclaw2/db';
import { withRoute } from '@/lib/api-gate';

/**
 * Admin endpoint — list the pending review queue. Per-proposal actions live
 * in /[id]/apply and /[id]/reject.
 */
export const GET = withRoute({ auth: 'admin' }, async () => {
  const pending = await listPendingProposedEdits();
  return NextResponse.json({
    pending: pending.map((p) => ({
      id: p.id,
      slug: p.slug,
      title: p.title,
      proposedBy: p.proposedBy,
      rationale: p.rationale,
      createdAt: p.createdAt,
      previousId: p.previousId,
      contentTextPreview: p.contentText.slice(0, 1000),
    })),
  });
});
