import { auth } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { db, sql } from '@chemclaw2/db';
import { rateLimit } from '@/lib/rate-limit';

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Release a single per-step approval gate. Owner-scoped: the user must have
 * created the campaign. Flips requires_approval=false; the worker poll will
 * pick the step up on its next 5-minute sweep (or sooner via next_retry_at).
 */
export async function POST(
  _req: Request,
  { params }: { params: Promise<{ id: string; idx: string }> },
) {
  const { userId } = await auth();
  if (!userId) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const { limited } = await rateLimit(`campaign:${userId}`, 30, 60_000);
  if (limited) return NextResponse.json({ error: 'Too many requests' }, { status: 429 });

  const { id, idx } = await params;
  if (!UUID_RE.test(id)) return NextResponse.json({ error: 'invalid campaign id' }, { status: 400 });
  const stepIdx = Number(idx);
  if (!Number.isInteger(stepIdx) || stepIdx < 0) {
    return NextResponse.json({ error: 'invalid step index' }, { status: 400 });
  }

  // Single UPDATE … RETURNING enforces ownership + idempotency.
  // Only flips when the step exists, belongs to a campaign owned by the user,
  // and is still gated. next_retry_at=NOW() so the worker sweep picks it up.
  const rows = await db.execute<{ id: string }>(sql`
    UPDATE campaign_steps cs
       SET requires_approval = false, next_retry_at = NOW()
      FROM synthesis_campaigns sc
     WHERE cs.campaign_id = sc.id
       AND sc.id = ${id}::uuid
       AND sc.created_by = ${userId}
       AND cs.step_idx = ${stepIdx}
       AND cs.requires_approval = true
       AND cs.status = 'pending'
    RETURNING cs.id
  `);
  if (rows.length === 0) {
    return NextResponse.json(
      { error: 'Step not found, not owned by you, already approved, or no longer pending' },
      { status: 404 },
    );
  }
  return NextResponse.json({ approved: true, stepIdx });
}
