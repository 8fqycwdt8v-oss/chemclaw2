const windows = new Map<string, number[]>();

/**
 * Sliding-window in-process rate limiter.
 * Suitable for single-machine deployments (Fly.io with 1 machine).
 * State resets on restart — acceptable for a pilot-scale system.
 *
 * Returns { limited: true } when the caller exceeds maxRequests within windowMs.
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
    if (timestamps.length === 0) {
      windows.delete(key);
    } else {
      windows.set(key, timestamps);
    }
    return { limited: true };
  }
  timestamps.push(now);
  windows.set(key, timestamps);
  return { limited: false };
}
