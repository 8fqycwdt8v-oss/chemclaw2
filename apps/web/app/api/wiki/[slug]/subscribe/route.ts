import { auth } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { getWikiPage, subscribeToWikiPage, unsubscribeFromWikiPage } from '@chemclaw2/db';
import { rateLimit } from '@/lib/rate-limit';
import { isValidSlug } from '@/lib/validation';
import { withApiContext } from '@/lib/api-context';
import { logger } from '@chemclaw2/observability';

export async function POST(_req: Request, { params }: { params: Promise<{ slug: string }> }) {
  return withApiContext(async () => {
    const { userId } = await auth();
    if (!userId) {
      logger.info('auth_denied', { route: 'wiki_subscribe', method: 'POST' });
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }
    const { limited } = await rateLimit(`wiki:${userId}`, 20, 60_000);
    if (limited) {
      logger.warn('rate_limit_hit', { route: 'wiki_subscribe', method: 'POST', user_id: userId });
      return NextResponse.json({ error: 'Too many requests' }, { status: 429 });
    }

    const { slug } = await params;
    if (!isValidSlug(slug)) {
      logger.info('validation_rejected', { route: 'wiki_subscribe', field: 'slug', reason: 'shape' });
      return NextResponse.json({ error: 'Invalid slug' }, { status: 400 });
    }
    const page = await getWikiPage(slug).catch((err) => {
      logger.error('get_wiki_page_failed', { slug, op: 'subscribe' }, err);
      throw err;
    });
    if (!page) return NextResponse.json({ error: 'Not found' }, { status: 404 });

    await subscribeToWikiPage(userId, page.id).catch((err) => {
      logger.error('subscribe_failed', { slug, page_id: page.id, user_id: userId }, err);
      throw err;
    });
    return NextResponse.json({ subscribed: true });
  });
}

export async function DELETE(_req: Request, { params }: { params: Promise<{ slug: string }> }) {
  return withApiContext(async () => {
    const { userId } = await auth();
    if (!userId) {
      logger.info('auth_denied', { route: 'wiki_subscribe', method: 'DELETE' });
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }
    const { limited } = await rateLimit(`wiki:${userId}`, 20, 60_000);
    if (limited) {
      logger.warn('rate_limit_hit', { route: 'wiki_subscribe', method: 'DELETE', user_id: userId });
      return NextResponse.json({ error: 'Too many requests' }, { status: 429 });
    }

    const { slug } = await params;
    if (!isValidSlug(slug)) {
      logger.info('validation_rejected', { route: 'wiki_subscribe', field: 'slug', reason: 'shape' });
      return NextResponse.json({ error: 'Invalid slug' }, { status: 400 });
    }
    const page = await getWikiPage(slug).catch((err) => {
      logger.error('get_wiki_page_failed', { slug, op: 'unsubscribe' }, err);
      throw err;
    });
    if (!page) return NextResponse.json({ error: 'Not found' }, { status: 404 });

    await unsubscribeFromWikiPage(userId, page.id).catch((err) => {
      logger.error('unsubscribe_failed', { slug, page_id: page.id, user_id: userId }, err);
      throw err;
    });
    return NextResponse.json({ subscribed: false });
  });
}
