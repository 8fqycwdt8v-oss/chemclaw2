import { UUID_RE } from '@/lib/validation';
import { NextResponse } from 'next/server';
import { upsertFeedback } from '@chemclaw2/db';
import { requireUserWithRateLimit } from '@/lib/api-gate';
import { withApiContext } from '@/lib/api-context';
import { logger } from '@chemclaw2/observability';

const MAX_REASON_LEN = 1000;

export async function POST(req: Request) {
  return withApiContext(async () => {
    const gate = await requireUserWithRateLimit('feedback', 60, 60_000);
    if (gate instanceof NextResponse) return gate;
    const { userId } = gate;

    let body: { sessionId?: unknown; turnIndex?: unknown; score?: unknown; reason?: unknown };
    try {
      body = (await req.json()) as typeof body;
    } catch (err) {
      logger.warn('json_parse_failed', { route: 'feedback' }, err);
      return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
    }

    if (typeof body.sessionId !== 'string' || !UUID_RE.test(body.sessionId)) {
      logger.info('validation_rejected', { route: 'feedback', field: 'sessionId', reason: 'shape' });
      return NextResponse.json({ error: 'sessionId must be a UUID' }, { status: 400 });
    }
    if (!Number.isInteger(body.turnIndex) || (body.turnIndex as number) < 0) {
      logger.info('validation_rejected', { route: 'feedback', field: 'turnIndex', reason: 'shape' });
      return NextResponse.json({ error: 'turnIndex must be a non-negative integer' }, { status: 400 });
    }
    if (body.score !== 1 && body.score !== -1) {
      logger.info('validation_rejected', { route: 'feedback', field: 'score', reason: 'enum' });
      return NextResponse.json({ error: 'score must be 1 or -1' }, { status: 400 });
    }
    let reason: string | null = null;
    if (body.reason !== undefined && body.reason !== null) {
      if (typeof body.reason !== 'string' || body.reason.length > MAX_REASON_LEN) {
        logger.info('validation_rejected', { route: 'feedback', field: 'reason', reason: 'shape' });
        return NextResponse.json({ error: 'reason must be a string ≤1000 chars' }, { status: 400 });
      }
      reason = body.reason.trim() || null;
    }

    const { id } = await upsertFeedback(
      body.sessionId,
      body.turnIndex as number,
      userId,
      body.score as 1 | -1,
      reason,
    ).catch((err) => {
      logger.error('upsert_feedback_failed', { session_id: body.sessionId as string, turn: body.turnIndex as number, user_id: userId }, err);
      throw err;
    });
    return NextResponse.json({ id });
  });
}
