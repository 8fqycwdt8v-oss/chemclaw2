import { auth, currentUser } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { listPendingProposedEdits } from '@chemclaw2/db';

/**
 * Wave-3c admin endpoint — list the pending review queue.
 * Per-proposal apply/reject actions live in /[id]/apply and /[id]/reject.
 */
export async function GET() {
  const { userId } = await auth();
  if (!userId) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const user = await currentUser();
  const role = (user?.publicMetadata as { role?: string } | undefined)?.role;
  if (role !== 'admin') {
    return NextResponse.json({ error: 'Forbidden — admin role required' }, { status: 403 });
  }
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
