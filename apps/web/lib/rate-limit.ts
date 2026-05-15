const windows = new Map<string, number[]>();

/**
 * Sliding-window in-process rate limiter.
 * Suitable for single-machine deployments (Fly.io with 1 machine).
 * State resets on restart — acceptable for a pilot-scale system.
 *
 * Returns { limited: true } when the caller exceeds maxRequests within windowMs.
 * Note: stale map entries (idle keys) are not eagerly pruned; acceptable at pilot scale.
 */
export function rateLimit(
  key: string,
  maxRequests: number,
  windowMs: number,
): { limited: boolean } {
  const now = Date.now();
  const cutoff = now - windowMs;
  const timestamps = (windows.get(key) ?? []).filter((t) => t > cutoff);
  if (timestamps.length >= maxRequests) {
    windows.set(key, timestamps);
    return { limited: true };
  }
  timestamps.push(now);
  windows.set(key, timestamps);
  return { limited: false };
}
