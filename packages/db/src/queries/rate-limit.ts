import { sql } from 'drizzle-orm';
import { db } from '../client';
import { rateLimits } from '../schema/rate-limits';

/**
 * Fixed-window Postgres-backed rate limiter.
 * Shared across all app instances — safe for multi-machine deployments.
 * ON CONFLICT DO UPDATE takes a row lock before executing, making the increment atomic.
 */
export async function pgRateLimit(
  key: string,
  maxRequests: number,
  windowMs: number,
): Promise<{ limited: boolean }> {
  const windowStart = Math.floor(Date.now() / windowMs) * windowMs;

  const [row] = await db
    .insert(rateLimits)
    .values({ key, windowStart, count: 1 })
    .onConflictDoUpdate({
      target: [rateLimits.key, rateLimits.windowStart],
      set: { count: sql`${rateLimits.count} + 1` },
    })
    .returning({ count: rateLimits.count });

  return { limited: row.count > maxRequests };
}
