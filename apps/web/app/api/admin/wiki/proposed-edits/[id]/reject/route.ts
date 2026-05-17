import { z } from 'zod';
import { NextResponse } from 'next/server';
import { markProposedEditRejected } from '@chemclaw2/db';
import { withRouteParams, errorResponse } from '@/lib/api-gate';
import { UUID_RE } from '@/lib/validation';

const RejectBody = z.object({
  comment: z.string().min(1).max(2000, 'comment (1-2000 chars) is required'),
});

/**
 * Admin endpoint — reject a pending proposal with a required reviewer comment
 * so the audit trail captures why the change was declined.
 */
export const POST = withRouteParams<{ id: string }, typeof RejectBody>(
  { auth: 'admin', rateLimit: { key: 'admin-proposed-edits', max: 60, windowMs: 60_000 }, body: RejectBody },
  async ({ userId, params, body }) => {
    if (!UUID_RE.test(params.id)) return errorResponse('id must be a UUID', 400);
    const { found } = await markProposedEditRejected(params.id, userId, body.comment);
    if (!found) return errorResponse('Not found or already reviewed', 404);
    return NextResponse.json({ ok: true });
  },
);
