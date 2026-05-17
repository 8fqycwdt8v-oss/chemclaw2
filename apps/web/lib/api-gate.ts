import { auth } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { rateLimit } from './rate-limit';

export async function requireUserWithRateLimit(
  key: string,
  max: number,
  windowMs: number,
  rateLimitedMessage = 'Too many requests',
): Promise<{ userId: string } | NextResponse> {
  const { userId } = await auth();
  if (!userId) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const { limited } = await rateLimit(`${key}:${userId}`, max, windowMs);
  if (limited) {
    return NextResponse.json(
      { error: rateLimitedMessage },
      { status: 429, headers: { 'Retry-After': '60' } },
    );
  }
  return { userId };
}
