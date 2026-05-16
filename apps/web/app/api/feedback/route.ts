import { UUID_RE } from '@/lib/validation';
import { auth } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { upsertFeedback } from '@chemclaw2/db';
import { rateLimit } from '@/lib/rate-limit';

const MAX_REASON_LEN = 1000;

export async function POST(req: Request) {
  const { userId } = await auth();
  if (!userId) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const { limited } = await rateLimit(`feedback:${userId}`, 60, 60_000);
  if (limited) {
    return NextResponse.json({ error: 'Too many requests' }, { status: 429, headers: { 'Retry-After': '60' } });
  }

  let body: { sessionId?: unknown; turnIndex?: unknown; score?: unknown; reason?: unknown };
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
  }

  if (typeof body.sessionId !== 'string' || !UUID_RE.test(body.sessionId)) {
    return NextResponse.json({ error: 'sessionId must be a UUID' }, { status: 400 });
  }
  if (!Number.isInteger(body.turnIndex) || (body.turnIndex as number) < 0) {
    return NextResponse.json({ error: 'turnIndex must be a non-negative integer' }, { status: 400 });
  }
  if (body.score !== 1 && body.score !== -1) {
    return NextResponse.json({ error: 'score must be 1 or -1' }, { status: 400 });
  }
  let reason: string | null = null;
  if (body.reason !== undefined && body.reason !== null) {
    if (typeof body.reason !== 'string' || body.reason.length > MAX_REASON_LEN) {
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
  );
  return NextResponse.json({ id });
}
