import { NextResponse } from 'next/server';
import { getWikiPage, subscribeToWikiPage, unsubscribeFromWikiPage } from '@chemclaw2/db';
import { withRouteParams, errorResponse } from '@/lib/api-gate';
import { isValidSlug } from '@/lib/validation';

// Response shape is identical regardless of slug existence so the endpoint
// can't be used to enumerate slugs within the rate-limit budget.
export const POST = withRouteParams<{ slug: string }>(
  { rateLimit: { key: 'wiki', max: 20, windowMs: 60_000 } },
  async ({ userId, params }) => {
    if (!isValidSlug(params.slug)) return errorResponse('Invalid slug', 400);
    const page = await getWikiPage(params.slug);
    if (page) await subscribeToWikiPage(userId, page.id);
    return NextResponse.json({ subscribed: true });
  },
);

export const DELETE = withRouteParams<{ slug: string }>(
  { rateLimit: { key: 'wiki', max: 20, windowMs: 60_000 } },
  async ({ userId, params }) => {
    if (!isValidSlug(params.slug)) return errorResponse('Invalid slug', 400);
    const page = await getWikiPage(params.slug);
    if (page) await unsubscribeFromWikiPage(userId, page.id);
    return NextResponse.json({ subscribed: false });
  },
);
