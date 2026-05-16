import { auth, currentUser } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import {
  getProposedEdit,
  markProposedEditApplied,
  upsertWikiPage,
} from '@chemclaw2/db';
import { embedTexts } from '@/lib/embeddings';

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Wave-3c admin endpoint — apply a pending proposal. Looks up the proposed
 * row, replays it through upsertWikiPage (the same write path live edits use),
 * then marks the proposal applied with the resulting page id.
 *
 * Optional body: { comment?: string } — recorded in review_comment for the
 * audit trail.
 */
export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { userId } = await auth();
  if (!userId) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const user = await currentUser();
  const role = (user?.publicMetadata as { role?: string } | undefined)?.role;
  if (role !== 'admin') {
    return NextResponse.json({ error: 'Forbidden — admin role required' }, { status: 403 });
  }

  const { id } = await params;
  if (!UUID_RE.test(id)) return NextResponse.json({ error: 'id must be a UUID' }, { status: 400 });

  let body: { comment?: unknown } = {};
  try {
    body = req.body ? (await req.json()) as typeof body : {};
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
  }
  if (body.comment !== undefined
      && (typeof body.comment !== 'string' || body.comment.length > 2000)) {
    return NextResponse.json({ error: 'comment must be a string ≤2000 chars' }, { status: 400 });
  }

  const proposal = await getProposedEdit(id);
  if (!proposal) return NextResponse.json({ error: 'Not found' }, { status: 404 });
  if (proposal.status !== 'pending') {
    return NextResponse.json(
      { error: `Cannot apply: proposal is already ${proposal.status}` },
      { status: 409 },
    );
  }

  // Replay through the same wiki write path; preserves citations + content +
  // re-chunks + re-embeds in one transaction. Reviewer becomes the author of
  // the live page (proposed_by stays on the audit row).
  const pageId = await upsertWikiPage(
    proposal.slug,
    proposal.title,
    proposal.content as Record<string, unknown>,
    proposal.contentText,
    userId,
    proposal.citations as Array<{ citationId: string; sourceType: string; sourceId?: string; label: string }>,
    embedTexts,
  );

  const { found } = await markProposedEditApplied(
    id,
    userId,
    pageId,
    typeof body.comment === 'string' ? body.comment : undefined,
  );
  if (!found) {
    // Pathological race: someone else marked it superseded between our read
    // and our write. The wiki page write still happened (idempotent upsert
    // by slug), so the world is consistent — just report.
    return NextResponse.json(
      { ok: true, page_id: pageId, note: 'Wiki updated but proposal row was no longer pending.' },
    );
  }
  return NextResponse.json({ ok: true, page_id: pageId });
}
