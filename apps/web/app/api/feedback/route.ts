import { z } from 'zod';
import { NextResponse } from 'next/server';
import { upsertFeedback } from '@chemclaw2/db';
import { withRoute } from '@/lib/api-gate';
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
    const reason = body.reason ? body.reason.trim() || null : null;
    const { id } = await upsertFeedback(body.sessionId, body.turnIndex, userId, body.score, reason);
    return NextResponse.json({ id });
  },
);
