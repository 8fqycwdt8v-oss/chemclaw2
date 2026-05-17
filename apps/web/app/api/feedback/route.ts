import { z } from 'zod';
import { NextResponse } from 'next/server';
import { upsertFeedback, sessionBelongsToUser } from '@chemclaw2/db';
import { withRoute, errorResponse } from '@/lib/api-gate';
import { UUID_RE } from '@/lib/validation';

const MAX_REASON_LEN = 1000;

const FeedbackBody = z.object({
  sessionId: z.string().refine((s) => UUID_RE.test(s), 'sessionId must be a UUID'),
  turnIndex: z.number().int().nonnegative({ message: 'turnIndex must be a non-negative integer' }),
  score: z.union([z.literal(1), z.literal(-1)], { message: 'score must be 1 or -1' }),
  reason: z.string().max(MAX_REASON_LEN, 'reason must be a string ≤1000 chars').nullish(),
});

export const POST = withRoute(
  { rateLimit: { key: 'feedback', max: 60, windowMs: 60_000 }, body: FeedbackBody },
  async ({ userId, body }) => {
    // Verify the session belongs to the caller. Without this any signed-in
    // user could attach feedback rows to any sessionId — the row is keyed
    // to their userId, but session_id would reference a session they don't
    // own (polluting analytics keyed on session_id).
    if (!(await sessionBelongsToUser(body.sessionId, userId))) {
      return errorResponse('Session not found', 404);
    }
    const reason = body.reason ? body.reason.trim() || null : null;
    const { id } = await upsertFeedback(body.sessionId, body.turnIndex, userId, body.score, reason);
    return NextResponse.json({ id });
  },
);
