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
import { withApiContext } from '@/lib/api-context';
import { logger } from '@chemclaw2/observability';

/**
 * Wave-3c admin endpoint — apply a pending proposal. Wave-3h made the apply
 * race-safe and added a defence-in-depth citation re-validation step.
 *
 * Order matters:
 *   1. tryClaimProposedEditForApply — atomically moves status pending→applied
 *      inside a `SELECT … FOR UPDATE` transaction.
 *   2. Re-validate citations on the staged content (defence-in-depth).
 *   3. upsertWikiPage to publish the content.
 *   4. setAppliedPageId to link the proposal to the materialized page id.
 *
 * On any failure after step 1, we `rollbackApplyClaim` so the proposal
 * returns to pending and a retry can succeed.
 */
export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  return withApiContext(async () => {
    const gate = await requireAdminApi();
    if (gate instanceof NextResponse) return gate;
    const { userId } = gate;

    const { id } = await params;
    if (!UUID_RE.test(id)) {
      logger.info('validation_rejected', { route: 'admin_apply', field: 'id', reason: 'shape' });
      return NextResponse.json({ error: 'id must be a UUID' }, { status: 400 });
    }

    let body: { comment?: unknown } = {};
    try {
      body = req.body ? (await req.json()) as typeof body : {};
    } catch (err) {
      logger.warn('json_parse_failed', { route: 'admin_apply' }, err);
      return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
    }
    if (body.comment !== undefined
        && (typeof body.comment !== 'string' || body.comment.length > 2000)) {
      logger.info('validation_rejected', { route: 'admin_apply', field: 'comment', reason: 'shape' });
      return NextResponse.json({ error: 'comment must be a string ≤2000 chars' }, { status: 400 });
    }
    const comment = typeof body.comment === 'string' ? body.comment : undefined;

    const rollback = async (phase: string, err?: unknown) => {
      try {
        await rollbackApplyClaim(id);
      } catch (rollbackErr) {
        logger.error('rollback_apply_claim_failed', { proposal_id: id, phase }, rollbackErr);
      }
      if (err !== undefined) logger.error('admin_apply_phase_failed', { proposal_id: id, phase, admin_id: userId }, err);
    };

    // 1. Atomic claim — row-lock + status→applied in one transaction.
    const proposal = await tryClaimProposedEditForApply(id, userId, comment).catch((err) => {
      logger.error('try_claim_proposed_edit_failed', { proposal_id: id, admin_id: userId }, err);
      throw err;
    });
    if (!proposal) {
      return NextResponse.json(
        { error: 'Conflict: proposal is not pending. Another reviewer may have applied, rejected, or superseded it.' },
        { status: 409 },
      );
    }

    // 2. Re-validate citations + Tiptap shape on the staged content.
    const citations = proposal.citations as CitationInput[];
    const v = validateCitations(proposal.contentText, citations);
    if (!v.ok) {
      logger.warn('admin_apply_citation_validation_failed', { proposal_id: id, slug: proposal.slug, reason: v.reason });
      await rollback('citation_validation');
      return NextResponse.json(
        { error: `Citation validation failed: ${v.reason}` },
        { status: 400 },
      );
    }
    if (!isValidTiptapDoc(proposal.content)) {
      logger.warn('admin_apply_invalid_tiptap', { proposal_id: id, slug: proposal.slug });
      await rollback('tiptap_validation');
      return NextResponse.json(
        { error: 'Proposal content is not a valid Tiptap doc' },
        { status: 400 },
      );
    }

    // 3. Publish through the canonical wiki write path.
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
      await rollback('upsert_wiki_page', err);
      return NextResponse.json(
        { error: 'Wiki write failed — proposal returned to pending. Try again.' },
        { status: 500 },
      );
    }

    // 4. Link the proposal to the live page.
    await setAppliedPageId(id, pageId).catch((err) => {
      logger.error('set_applied_page_id_failed', { proposal_id: id, page_id: pageId }, err);
      throw err;
    });
    logger.info('admin_apply_complete', { proposal_id: id, slug: proposal.slug, page_id: pageId, admin_id: userId });
    return NextResponse.json({ ok: true, page_id: pageId });
  });
}
