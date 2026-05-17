import { z } from 'zod';
import { NextResponse } from 'next/server';
import { getWikiPage, setCitationDisputed } from '@chemclaw2/db';
import { withRouteParams, errorResponse } from '@/lib/api-gate';
import { isValidSlug } from '@/lib/validation';

const DisputeBody = z.object({
  disputed: z.boolean({ message: 'disputed (boolean) is required' }),
});

export const POST = withRouteParams<{ slug: string; cid: string }, typeof DisputeBody>(
  { rateLimit: { key: 'wiki', max: 20, windowMs: 60_000 }, body: DisputeBody },
  async ({ params, body }) => {
    if (!isValidSlug(params.slug) || params.cid.length === 0 || params.cid.length > 200) {
      return errorResponse('Invalid slug or citation id', 400);
    }
    // Normalized response: identical shape whether slug+citation exist or
    // not, so this can't be used to enumerate (slug, cid) pairs.
    const page = await getWikiPage(params.slug);
    if (page) await setCitationDisputed(page.id, params.cid, body.disputed);
    return NextResponse.json({ disputed: body.disputed });
  },
);
