import { UUID_RE } from '@/lib/validation';
import { NextResponse } from 'next/server';
import {
  getProposedEdit,
  markProposedEditApplied,
  upsertWikiPage,
} from '@chemclaw2/db';
import { embedTexts } from '@/lib/embeddings';
import { requireAdminApi } from '@/lib/auth';

/**
 * Wave-3c admin endpoint — apply a pending proposal. Looks up the proposed
 * row, replays it through upsertWikiPage (the same write path live edits use),
 * then marks the proposal applied with the resulting page id.
 *
 * Optional body: { comment?: string } — recorded in review_comment for the
 * audit trail.
 */
export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const gate = await requireAdminApi();
  if (gate instanceof NextResponse) return gate;
  const { userId } = gate;

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
    // Wave-3f bug-fix: another admin applied/superseded between our read and
    // our write. The wiki page write still happened (idempotent upsert by
    // slug), but the audit trail records only the first reviewer — this
    // reviewer's click is lost. Surface as 409 so the UI tells the user the
    // truth instead of "Applied" twice.
    return NextResponse.json(
      {
        error: 'Conflict: proposal was no longer pending when applied. Another reviewer beat you to it.',
        page_id: pageId,
      },
      { status: 409 },
    );
  }
  return NextResponse.json({ ok: true, page_id: pageId });
}
