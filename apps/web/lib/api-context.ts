import { headers } from 'next/headers';
import { auth } from '@clerk/nextjs/server';
import { runWithRequestContext, mintRequestId } from '@chemclaw2/observability';

/**
 * Wrap a route handler so log lines emitted during the request carry a
 * `request_id` and `user_id`. The middleware mints the request id and forwards
 * it via `x-request-id`; this helper hoists it into AsyncLocalStorage for the
 * duration of the handler.
 *
 * Auth() is called here so the user id propagates without each route having
 * to remember to set it on the context. Routes still call auth() themselves
 * (Clerk caches the result for the request).
 */
export async function withApiContext<T>(fn: () => Promise<T>): Promise<T> {
  const h = await headers();
  const requestId = h.get('x-request-id') ?? mintRequestId();
  let userId: string | undefined;
  try {
    const a = await auth();
    userId = a.userId ?? undefined;
  } catch {
    // auth() can throw if Clerk middleware wasn't run for this route — treat
    // the request as anonymous, which is the same outcome the route sees.
  }
  return runWithRequestContext({ requestId, userId }, fn);
}
