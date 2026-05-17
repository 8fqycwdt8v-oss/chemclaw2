import { z } from 'zod';
import { NextResponse } from 'next/server';
import {
  countUnreadSubscriptions,
  listPendingCampaignNotifications,
  acknowledgeCampaignNotifications,
} from '@chemclaw2/db';
import { withRoute } from '@/lib/api-gate';
import { UUID_RE } from '@/lib/validation';

/**
 * GET: read-only list of pending notifications for the signed-in user.
 *   - wiki_unread: count of pages with new revisions on subscribed pages
 *   - campaigns: terminal-state transitions on campaigns the user owns
 *     that they have not yet acknowledged
 *
 * POST: acknowledge a list of campaign IDs (sets notified_at = NOW()).
 */
export const GET = withRoute(
  { rateLimit: { key: 'notifications', max: 120, windowMs: 60_000 } },
  async ({ userId }) => {
    const wikiUnread = await countUnreadSubscriptions(userId).catch(() => 0);
    const campaigns = await listPendingCampaignNotifications(userId);
    return NextResponse.json({
      wiki_unread: wikiUnread,
      campaigns: campaigns.map((c) => ({
        id: c.id,
        targetSmiles: c.targetSmiles,
        status: c.status,
        wikiPageId: c.wikiPageId,
        updatedAt: c.updatedAt,
      })),
    });
  },
);

const AckBody = z.object({
  campaignIds: z
    .array(z.string().refine((s) => UUID_RE.test(s), 'every campaignId must be a UUID'))
    .min(1, 'campaignIds must be a 1-100 element array')
    .max(100, 'campaignIds must be a 1-100 element array'),
});

export const POST = withRoute(
  { rateLimit: { key: 'notifications', max: 120, windowMs: 60_000 }, body: AckBody },
  async ({ userId, body }) => {
    const acknowledged = await acknowledgeCampaignNotifications(userId, body.campaignIds);
    return NextResponse.json({ acknowledged });
  },
);
