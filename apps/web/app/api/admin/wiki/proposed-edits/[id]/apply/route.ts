import { z } from 'zod';
import { NextResponse } from 'next/server';
import { applyProposedEdit, upsertWikiPage } from '@chemclaw2/db';
import { validateCitations, type CitationInput } from '@chemclaw2/agent-tools';
import { embedTexts } from '@/lib/embeddings';
import { withRouteParams, errorResponse } from '@/lib/api-gate';
import { UUID_RE, isValidTiptapDoc } from '@/lib/validation';

const ApplyBody = z
  .object({ comment: z.string().max(2000, 'comment must be a string ≤2000 chars').optional() })
  .optional()
  .default({});

/**
 * Apply a pending proposal. The route is a thin orchestration that supplies
 * the validators + canonical wiki-write to `applyProposedEdit` in the db
 * layer. The state machine (claim → validate → upsert → setAppliedPageId,
 * with compensating rollback on any failure) lives there.
 */
export const POST = withRouteParams<{ id: string }, typeof ApplyBody>(
  { auth: 'admin', rateLimit: { key: 'admin-proposed-edits', max: 60, windowMs: 60_000 }, body: ApplyBody },
  async ({ userId, params, body }) => {
    if (!UUID_RE.test(params.id)) return errorResponse('id must be a UUID', 400);

    const result = await applyProposedEdit(params.id, userId, body?.comment, {
      validate(proposal) {
        const citations = proposal.citations as CitationInput[];
        const v = validateCitations(proposal.contentText, citations);
        if (!v.ok) {
          return { ok: false, status: 400, error: `Citation validation failed: ${v.reason}` };
        }
        if (!isValidTiptapDoc(proposal.content)) {
          return { ok: false, status: 400, error: 'Proposal content is not a valid Tiptap doc' };
        }
        return { ok: true };
      },
      upsert(proposal, reviewerId) {
        const citations = proposal.citations as CitationInput[];
        return upsertWikiPage(
          proposal.slug,
          proposal.title,
          proposal.content,
          proposal.contentText,
          reviewerId,
          citations,
          embedTexts,
        );
      },
    });

    if (!result.ok) return errorResponse(result.error, result.status);
    return NextResponse.json({ ok: true, page_id: result.pageId });
  },
);
