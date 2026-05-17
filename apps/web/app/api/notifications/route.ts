import { UUID_RE } from '@/lib/validation';
import { auth } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { db, sql, countUnreadSubscriptions, synthesisCampaigns, and, eq, inArray, isNull } from '@chemclaw2/db';
import { rateLimit } from '@/lib/rate-limit';
import { withApiContext } from '@/lib/api-context';
import { logger } from '@chemclaw2/observability';

/**
 * GET: read-only list of pending notifications for the signed-in user.
 *   - wiki_unread: count of pages with new revisions on subscribed pages
 *   - campaigns: terminal-state transitions on campaigns the user owns
 *     that they have not yet acknowledged
 *
 * POST: acknowledge a list of campaign IDs (sets notified_at = NOW()).
 *
 * Followup #6: split from a single GET that mutated state. Idempotent GET +
 * explicit POST avoids the prefetch/race-disappear failure mode.
 */
export async function GET() {
  return withApiContext(async () => {
    const { userId } = await auth();
    if (!userId) {
      logger.info('auth_denied', { route: 'notifications', method: 'GET' });
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const { limited } = await rateLimit(`notifications:${userId}`, 120, 60_000);
    if (limited) {
      logger.warn('rate_limit_hit', { route: 'notifications', method: 'GET', user_id: userId });
      return NextResponse.json({ error: 'Too many requests' }, { status: 429 });
    }

    const wikiUnread = await countUnreadSubscriptions(userId).catch((err) => {
      logger.warn('count_unread_subscriptions_failed', { user_id: userId }, err);
      return 0;
    });

    const campaigns = await db.execute<{
      id: string;
      target_smiles: string | null;
      status: string;
      wiki_page_id: string | null;
      updated_at: string;
    }>(sql`
      SELECT id, target_smiles, status, wiki_page_id, updated_at
        FROM synthesis_campaigns
       WHERE created_by = ${userId}
         AND status IN ('complete', 'failed')
         AND notified_at IS NULL
    `).catch((err) => {
      logger.error('notifications_campaigns_query_failed', { user_id: userId }, err);
      throw err;
    });

    return NextResponse.json({
      wiki_unread: wikiUnread,
      campaigns: campaigns.map((c) => ({
        id: c.id,
        targetSmiles: c.target_smiles,
        status: c.status,
        wikiPageId: c.wiki_page_id,
        updatedAt: c.updated_at,
      })),
    });
  });
}


export async function POST(req: Request) {
  return withApiContext(async () => {
    const { userId } = await auth();
    if (!userId) {
      logger.info('auth_denied', { route: 'notifications', method: 'POST' });
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const { limited } = await rateLimit(`notifications:${userId}`, 120, 60_000);
    if (limited) {
      logger.warn('rate_limit_hit', { route: 'notifications', method: 'POST', user_id: userId });
      return NextResponse.json({ error: 'Too many requests' }, { status: 429 });
    }

    let body: { campaignIds?: unknown };
    try {
      body = (await req.json()) as typeof body;
    } catch (err) {
      logger.warn('json_parse_failed', { route: 'notifications' }, err);
      return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
    }
    if (!Array.isArray(body.campaignIds) || body.campaignIds.length === 0 || body.campaignIds.length > 100) {
      logger.info('validation_rejected', { route: 'notifications', field: 'campaignIds', reason: 'shape' });
      return NextResponse.json({ error: 'campaignIds must be a 1-100 element array' }, { status: 400 });
    }
    if (!body.campaignIds.every((id) => typeof id === 'string' && UUID_RE.test(id))) {
      logger.info('validation_rejected', { route: 'notifications', field: 'campaignIds[]', reason: 'non_uuid' });
      return NextResponse.json({ error: 'every campaignId must be a UUID' }, { status: 400 });
    }

    const ids = body.campaignIds as string[];
    const acknowledged = await db
      .update(synthesisCampaigns)
      .set({ notifiedAt: new Date() })
      .where(and(
        eq(synthesisCampaigns.createdBy, userId),
        inArray(synthesisCampaigns.id, ids),
        isNull(synthesisCampaigns.notifiedAt),
      ))
      .returning({ id: synthesisCampaigns.id })
      .catch((err) => {
        logger.error('notifications_ack_failed', { user_id: userId, id_count: ids.length }, err);
        throw err;
      });
    return NextResponse.json({ acknowledged: acknowledged.map((r) => r.id) });
  });
}
