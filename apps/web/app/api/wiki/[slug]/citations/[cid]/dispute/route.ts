import { auth } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { getWikiPage, setCitationDisputed } from '@chemclaw2/db';
import { rateLimit } from '@/lib/rate-limit';
import { isValidSlug } from '@/lib/validation';
import { withApiContext } from '@/lib/api-context';
import { logger } from '@chemclaw2/observability';

export async function POST(
  req: Request,
  { params }: { params: Promise<{ slug: string; cid: string }> },
) {
  return withApiContext(async () => {
    const { userId } = await auth();
    if (!userId) {
      logger.info('auth_denied', { route: 'citation_dispute' });
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }
    const { limited } = await rateLimit(`wiki:${userId}`, 20, 60_000);
    if (limited) {
      logger.warn('rate_limit_hit', { route: 'citation_dispute', user_id: userId });
      return NextResponse.json({ error: 'Too many requests' }, { status: 429 });
    }

    const { slug, cid } = await params;
    if (!isValidSlug(slug) || cid.length === 0 || cid.length > 200) {
      logger.info('validation_rejected', { route: 'citation_dispute', field: 'slug_or_cid', reason: 'shape' });
      return NextResponse.json({ error: 'Invalid slug or citation id' }, { status: 400 });
    }
    const page = await getWikiPage(slug).catch((err) => {
      logger.error('get_wiki_page_failed', { slug, op: 'citation_dispute' }, err);
      throw err;
    });
    if (!page) return NextResponse.json({ error: 'Not found' }, { status: 404 });

    let body: { disputed?: unknown };
    try {
      body = (await req.json()) as typeof body;
    } catch (err) {
      logger.warn('json_parse_failed', { route: 'citation_dispute' }, err);
      return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
    }
    if (typeof body.disputed !== 'boolean') {
      logger.info('validation_rejected', { route: 'citation_dispute', field: 'disputed', reason: 'type' });
      return NextResponse.json({ error: 'disputed (boolean) is required' }, { status: 400 });
    }

    const { found } = await setCitationDisputed(page.id, cid, body.disputed).catch((err) => {
      logger.error('set_citation_disputed_failed', { slug, page_id: page.id, citation_id: cid, disputed: body.disputed }, err);
      throw err;
    });
    if (!found) return NextResponse.json({ error: 'Citation not found' }, { status: 404 });
    return NextResponse.json({ disputed: body.disputed });
  });
}
