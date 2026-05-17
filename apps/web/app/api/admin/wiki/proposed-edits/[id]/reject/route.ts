import { UUID_RE } from '@/lib/validation';
import { NextResponse } from 'next/server';
import { markProposedEditRejected } from '@chemclaw2/db';
import { requireAdminWithRateLimit } from '@/lib/api-gate';
import { withApiContext } from '@/lib/api-context';
import { logger } from '@chemclaw2/observability';

/**
 * Wave-3c admin endpoint — reject a pending proposal with a required
 * reviewer comment so the audit trail captures why the change was declined.
 */
export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  return withApiContext(async () => {
    const gate = await requireAdminWithRateLimit('proposed-edits', 60, 60_000);
    if (gate instanceof NextResponse) return gate;
    const { userId } = gate;

    const { id } = await params;
    if (!UUID_RE.test(id)) {
      logger.info('validation_rejected', { route: 'admin_reject', field: 'id', reason: 'shape' });
      return NextResponse.json({ error: 'id must be a UUID' }, { status: 400 });
    }

    let body: { comment?: unknown };
    try {
      body = (await req.json()) as typeof body;
    } catch (err) {
      logger.warn('json_parse_failed', { route: 'admin_reject' }, err);
      return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
    }
    if (typeof body.comment !== 'string' || body.comment.length === 0 || body.comment.length > 2000) {
      logger.info('validation_rejected', { route: 'admin_reject', field: 'comment', reason: 'shape' });
      return NextResponse.json({ error: 'comment (1-2000 chars) is required' }, { status: 400 });
    }

    const { found } = await markProposedEditRejected(id, userId, body.comment).catch((err) => {
      logger.error('mark_proposed_edit_rejected_failed', { proposal_id: id, admin_id: userId }, err);
      throw err;
    });
    if (!found) {
      return NextResponse.json({ error: 'Not found or already reviewed' }, { status: 404 });
    }
    logger.info('admin_reject_complete', { proposal_id: id, admin_id: userId });
    return NextResponse.json({ ok: true });
  });
}
