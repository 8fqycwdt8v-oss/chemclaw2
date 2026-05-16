import { auth } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { db, sql, countUnreadSubscriptions } from '@chemclaw2/db';
import { rateLimit } from '@/lib/rate-limit';

/**
 * Pull notifications for the signed-in user:
 *  - wiki-page updates on subscribed pages (count only — UI shows the badge,
 *    the user navigates to /wiki to see which page)
 *  - terminal-state transitions on campaigns they own (full events; marked
 *    read via notified_at = NOW() on read so each event surfaces once)
 *
 * Polled by ChatClient + (app) layout. Polling is the deliberate choice over
 * websocket per the v2 plan — 30s latency is acceptable for these events.
 */
export async function GET() {
  const { userId } = await auth();
  if (!userId) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const { limited } = await rateLimit(`notifications:${userId}`, 120, 60_000);
  if (limited) return NextResponse.json({ error: 'Too many requests' }, { status: 429 });

  const wikiUnread = await countUnreadSubscriptions(userId).catch(() => 0);

  // Terminal campaigns owned by the user that haven't been notified yet.
  // We mark them read in the same response to make the surface single-emit.
  const campaigns = await db.execute<{
    id: string;
    target_smiles: string | null;
    status: string;
    wiki_page_id: string | null;
    updated_at: string;
  }>(sql`
    UPDATE synthesis_campaigns
       SET notified_at = NOW()
     WHERE created_by = ${userId}
       AND status IN ('complete', 'failed')
       AND notified_at IS NULL
    RETURNING id, target_smiles, status, wiki_page_id, updated_at
  `);

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
}
