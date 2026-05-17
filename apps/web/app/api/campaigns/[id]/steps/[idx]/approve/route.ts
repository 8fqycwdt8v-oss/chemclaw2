import { UUID_RE } from '@/lib/validation';
import { auth } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { db, sql } from '@chemclaw2/db';
import { rateLimit } from '@/lib/rate-limit';
import { withApiContext } from '@/lib/api-context';
import { logger } from '@chemclaw2/observability';


/**
 * Release a single per-step approval gate. Owner-scoped: the user must have
 * created the campaign. Flips requires_approval=false; the worker poll will
 * pick the step up on its next 5-minute sweep (or sooner via next_retry_at).
 */
export async function POST(
  _req: Request,
  { params }: { params: Promise<{ id: string; idx: string }> },
) {
  return withApiContext(async () => {
    const { userId } = await auth();
    if (!userId) {
      logger.info('auth_denied', { route: 'campaign_step_approve' });
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }
    const { limited } = await rateLimit(`campaign:${userId}`, 30, 60_000);
    if (limited) {
      logger.warn('rate_limit_hit', { route: 'campaign_step_approve', user_id: userId });
      return NextResponse.json({ error: 'Too many requests' }, { status: 429 });
    }

    const { id, idx } = await params;
    if (!UUID_RE.test(id)) {
      logger.info('validation_rejected', { route: 'campaign_step_approve', field: 'id', reason: 'shape' });
      return NextResponse.json({ error: 'invalid campaign id' }, { status: 400 });
    }
    const stepIdx = Number(idx);
    if (!Number.isInteger(stepIdx) || stepIdx < 0) {
      logger.info('validation_rejected', { route: 'campaign_step_approve', field: 'idx', reason: 'shape' });
      return NextResponse.json({ error: 'invalid step index' }, { status: 400 });
    }

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
    `).catch((err) => {
      logger.error('campaign_step_approve_failed', { campaign_id: id, step_idx: stepIdx, user_id: userId }, err);
      throw err;
    });
    if (rows.length === 0) {
      return NextResponse.json(
        { error: 'Step not found, not owned by you, already approved, or no longer pending' },
        { status: 404 },
      );
    }
    logger.info('campaign_step_approved', { campaign_id: id, step_idx: stepIdx, user_id: userId });
    return NextResponse.json({ approved: true, stepIdx });
  });
}
