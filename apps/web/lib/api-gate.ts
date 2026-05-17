import { NextResponse } from 'next/server';
import { logger } from '@chemclaw2/observability';
import { auth } from '@clerk/nextjs/server';
import { requireAdminApi } from './auth';
import { rateLimit } from './rate-limit';

export async function requireUserWithRateLimit(
  key: string,
  max: number,
  windowMs: number,
  rateLimitedMessage = 'Too many requests',
): Promise<{ userId: string } | NextResponse> {
  const { userId } = await auth();
  if (!userId) {
    logger.info('auth_denied', { route: key });
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }
  const { limited } = await rateLimit(`${key}:${userId}`, max, windowMs);
  if (limited) {
    logger.warn('rate_limit_hit', { route: key, user_id: userId, max, window_ms: windowMs });
    return NextResponse.json(
      { error: rateLimitedMessage },
      { status: 429, headers: { 'Retry-After': '60' } },
    );
  }
  return { userId };
}

/**
 * Admin variant: enforces admin role AND applies a rate limit. Even admin
 * sessions need throttling — a leaked admin token shouldn't be a megaphone
 * for spamming apply/reject/budget mutations or scraping pending edits.
 */
export async function requireAdminWithRateLimit(
  key: string,
  max: number,
  windowMs: number,
): Promise<{ userId: string } | NextResponse> {
  const gate = await requireAdminApi();
  if (gate instanceof NextResponse) return gate;
  const { userId } = gate;
  const { limited } = await rateLimit(`admin:${key}:${userId}`, max, windowMs);
  if (limited) {
    logger.warn('rate_limit_hit', { route: `admin:${key}`, user_id: userId, max, window_ms: windowMs });
    return NextResponse.json(
      { error: 'Too many requests' },
      { status: 429, headers: { 'Retry-After': '60' } },
    );
  }
  return { userId };
}
