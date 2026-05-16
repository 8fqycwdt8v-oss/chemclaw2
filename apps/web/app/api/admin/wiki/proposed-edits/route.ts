import { NextResponse } from 'next/server';
import { listPendingProposedEdits } from '@chemclaw2/db';
import { requireAdminApi } from '@/lib/auth';

/**
 * Wave-3c admin endpoint — list the pending review queue.
 * Per-proposal apply/reject actions live in /[id]/apply and /[id]/reject.
 */
export async function GET() {
  const gate = await requireAdminApi();
  if (gate instanceof NextResponse) return gate;
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
}
