import { NextResponse } from 'next/server';
import { getWikiPage, subscribeToWikiPage, unsubscribeFromWikiPage } from '@chemclaw2/db';
import { requireUserWithRateLimit } from '@/lib/api-gate';
import { isValidSlug } from '@/lib/validation';
import { withApiContext } from '@/lib/api-context';
import { logger } from '@chemclaw2/observability';

export async function POST(_req: Request, { params }: { params: Promise<{ slug: string }> }) {
  return withApiContext(async () => {
    const gate = await requireUserWithRateLimit('wiki', 20, 60_000);
    if (gate instanceof NextResponse) return gate;
    const { userId } = gate;

    const { slug } = await params;
    if (!isValidSlug(slug)) {
      logger.info('validation_rejected', { route: 'wiki_subscribe', field: 'slug', reason: 'shape' });
      return NextResponse.json({ error: 'Invalid slug' }, { status: 400 });
    }
    // Normalized response: 200 regardless of whether the slug exists, so
    // this endpoint can't be used to enumerate slugs within the rate-limit
    // budget. No-op on the DB side if the page is missing.
    const page = await getWikiPage(slug).catch((err) => {
      logger.error('get_wiki_page_failed', { slug, op: 'subscribe' }, err);
      throw err;
    });
    if (page) {
      await subscribeToWikiPage(userId, page.id).catch((err) => {
        logger.error('subscribe_failed', { slug, page_id: page.id, user_id: userId }, err);
        throw err;
      });
    }
    return NextResponse.json({ subscribed: true });
  });
}

export async function DELETE(_req: Request, { params }: { params: Promise<{ slug: string }> }) {
  return withApiContext(async () => {
    const gate = await requireUserWithRateLimit('wiki', 20, 60_000);
    if (gate instanceof NextResponse) return gate;
    const { userId } = gate;

    const { slug } = await params;
    if (!isValidSlug(slug)) {
      logger.info('validation_rejected', { route: 'wiki_subscribe', field: 'slug', reason: 'shape' });
      return NextResponse.json({ error: 'Invalid slug' }, { status: 400 });
    }
    const page = await getWikiPage(slug).catch((err) => {
      logger.error('get_wiki_page_failed', { slug, op: 'unsubscribe' }, err);
      throw err;
    });
    if (page) {
      await unsubscribeFromWikiPage(userId, page.id).catch((err) => {
        logger.error('unsubscribe_failed', { slug, page_id: page.id, user_id: userId }, err);
        throw err;
      });
    }
    return NextResponse.json({ subscribed: false });
  });
}
