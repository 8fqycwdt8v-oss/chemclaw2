import { NextResponse } from 'next/server';
import { getWikiPage, subscribeToWikiPage, unsubscribeFromWikiPage } from '@chemclaw2/db';
import { withRouteParams, errorResponse } from '@/lib/api-gate';
import { isValidSlug } from '@/lib/validation';

export const POST = withRouteParams<{ slug: string }>(
  { rateLimit: { key: 'wiki', max: 20, windowMs: 60_000 } },
  async ({ userId, params }) => {
    if (!isValidSlug(params.slug)) return errorResponse('Invalid slug', 400);
    const page = await getWikiPage(params.slug);
    if (!page) return errorResponse('Not found', 404);
    await subscribeToWikiPage(userId, page.id);
    return NextResponse.json({ subscribed: true });
  },
);

export const DELETE = withRouteParams<{ slug: string }>(
  { rateLimit: { key: 'wiki', max: 20, windowMs: 60_000 } },
  async ({ userId, params }) => {
    if (!isValidSlug(params.slug)) return errorResponse('Invalid slug', 400);
    const page = await getWikiPage(params.slug);
    if (!page) return errorResponse('Not found', 404);
    await unsubscribeFromWikiPage(userId, page.id);
    return NextResponse.json({ subscribed: false });
  },
);
