import { sql } from 'drizzle-orm';
import { logger } from '@chemclaw2/observability';
import { db } from '../client';
import { rateLimits } from '../schema/rate-limits';

/**
 * Fixed-window Postgres-backed rate limiter.
 * Shared across all app instances — safe for multi-machine deployments.
 * ON CONFLICT DO UPDATE takes a row lock before executing, making the increment atomic.
 * Fails open (allows the request) if the database is unavailable, to avoid turning
 * a DB outage into a total API outage.
 */
export async function pgRateLimit(
  key: string,
  maxRequests: number,
  windowMs: number,
): Promise<{ limited: boolean }> {
  const windowStart = Math.floor(Date.now() / windowMs) * windowMs;

  try {
    const [row] = await db
      .insert(rateLimits)
      .values({ key, windowStart, count: 1 })
      .onConflictDoUpdate({
        target: [rateLimits.key, rateLimits.windowStart],
        set: { count: sql`${rateLimits.count} + 1` },
      })
      .returning({ count: rateLimits.count });

    // count reflects the post-increment value for this request (INSERT starts at 1).
    // Use > so that the maxRequests-th request is still allowed; only the (maxRequests+1)-th is blocked.
    if (!row) {
      logger.error('rate_limit_no_row_returned', { key, max_requests: maxRequests });
      return { limited: false };
    }
    return { limited: row.count > maxRequests };
  } catch (err) {
    // Fail open — a DB outage should degrade gracefully, not block all users.
    // This is a security-relevant bypass: every rate limit in the app stops
    // enforcing, so emit a loud signal so oncall sees the open window.
    logger.error('rate_limit_db_fail_open', { key, max_requests: maxRequests }, err);
    return { limited: false };
  }
}
