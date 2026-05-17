import { UUID_RE, isValidTiptapDoc } from '@/lib/validation';
import { NextResponse } from 'next/server';
import {
  upsertWikiPage,
  tryClaimProposedEditForApply,
  setAppliedPageId,
  rollbackApplyClaim,
} from '@chemclaw2/db';
import { validateCitations, type CitationInput } from '@chemclaw2/agent-tools';
import { embedTexts } from '@/lib/embeddings';
import { requireAdminApi } from '@/lib/auth';

/**
 * Wave-3c admin endpoint — apply a pending proposal. Wave-3h made the apply
 * race-safe and added a defence-in-depth citation re-validation step.
 *
 * Order matters:
 *   1. tryClaimProposedEditForApply — atomically moves status pending→applied
 *      inside a `SELECT … FOR UPDATE` transaction. A concurrent rejecter or
 *      second-apply blocks on the row lock and then sees the new status.
 *   2. Re-validate citations on the staged content (defence-in-depth — the
 *      staged row could in principle have been written via a path that
 *      bypassed citation validation; today only propose_wiki_edit writes
 *      it, but the apply route is the canonical trust boundary).
 *   3. upsertWikiPage to publish the content.
 *   4. setAppliedPageId to link the proposal to the materialized page id.
 *
 * On any failure after step 1, we `rollbackApplyClaim` so the proposal
 * returns to pending and a retry can succeed.
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
  const comment = typeof body.comment === 'string' ? body.comment : undefined;

  // 1. Atomic claim — row-lock + status→applied in one transaction.
  const proposal = await tryClaimProposedEditForApply(id, userId, comment);
  if (!proposal) {
    return NextResponse.json(
      { error: 'Conflict: proposal is not pending. Another reviewer may have applied, rejected, or superseded it.' },
      { status: 409 },
    );
  }

  // 2. Re-validate citations + Tiptap shape on the staged content.
  // Defence-in-depth: today only propose_wiki_edit writes the row, but the
  // apply route is the canonical trust boundary for what lands in
  // wiki_pages.content.
  const citations = proposal.citations as CitationInput[];
  const v = validateCitations(proposal.contentText, citations);
  if (!v.ok) {
    await rollbackApplyClaim(id);
    return NextResponse.json(
      { error: `Citation validation failed: ${v.reason}` },
      { status: 400 },
    );
  }
  if (!isValidTiptapDoc(proposal.content)) {
    await rollbackApplyClaim(id);
    return NextResponse.json(
      { error: 'Proposal content is not a valid Tiptap doc' },
      { status: 400 },
    );
  }

  // 3. Publish through the canonical wiki write path. Reviewer becomes the
  // author of the live page (proposed_by stays on the audit row).
  let pageId: string;
  try {
    pageId = await upsertWikiPage(
      proposal.slug,
      proposal.title,
      proposal.content,
      proposal.contentText,
      userId,
      citations,
      embedTexts,
    );
  } catch (err) {
    await rollbackApplyClaim(id);
    console.error('[apply] upsertWikiPage failed, claim rolled back:', err);
    return NextResponse.json(
      { error: 'Wiki write failed — proposal returned to pending. Try again.' },
      { status: 500 },
    );
  }

  // 4. Link the proposal to the live page.
  await setAppliedPageId(id, pageId);
  return NextResponse.json({ ok: true, page_id: pageId });
}
