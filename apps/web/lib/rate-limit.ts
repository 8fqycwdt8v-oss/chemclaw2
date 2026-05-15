import { pgRateLimit } from '@chemclaw2/db';

/**
 * Postgres-backed rate limiter — shared across all app instances.
 * Fixed-window semantics; windowMs is the window duration in milliseconds.
 */
export async function rateLimit(
  key: string,
  maxRequests: number,
  windowMs: number,
): Promise<{ limited: boolean }> {
  return pgRateLimit(key, maxRequests, windowMs);
}
