import { sql } from 'drizzle-orm';
import { logger } from '@chemclaw2/observability';
import { db } from '../client';
import { rateLimits } from '../schema/rate-limits';

/**
 * Fixed-window Postgres-backed rate limiter.
 * Shared across all app instances — safe for multi-machine deployments.
 * ON CONFLICT DO UPDATE takes a row lock before executing, making the increment atomic.
 *
 * Fails CLOSED on DB error: a stampede that stresses the DB is precisely
 * when rate limiting matters most. Returning limited:true keeps the
 * cost-amplification path gated and surfaces a 429 to the client. The
 * fail-open alternative left every per-user cap silently disabled.
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
      // No row returned is a Postgres-driver edge case (extremely rare).
      // Fail closed for consistency with the catch branch below.
      logger.error('rate_limit_no_row_returned_fail_closed', { key, max_requests: maxRequests });
      return { limited: true };
    }
    return { limited: row.count > maxRequests };
  } catch (err) {
    logger.error('rate_limit_db_fail_closed', { key, max_requests: maxRequests }, err);
    return { limited: true };
  }
}
