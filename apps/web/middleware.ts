import { NextResponse } from 'next/server';
import { clerkMiddleware, createRouteMatcher } from '@clerk/nextjs/server';
import { mintRequestId } from '@chemclaw2/observability';

const isPublicRoute = createRouteMatcher([
  '/sign-in(.*)',
  '/sign-up(.*)',
  '/api/health',
]);

const REQUEST_ID_HEADER = 'x-request-id';

export default clerkMiddleware(async (auth, req) => {
  if (!isPublicRoute(req)) {
    await auth.protect();
  }
  // Mint or trust an incoming request id. Edge middleware can't share an
  // AsyncLocalStorage with the Node runtime that runs route handlers, so we
  // propagate via the request/response headers — route handlers read the
  // request header on entry.
  const incoming = req.headers.get(REQUEST_ID_HEADER);
  const requestId = incoming && /^[A-Za-z0-9_-]{1,128}$/.test(incoming) ? incoming : mintRequestId();
  const requestHeaders = new Headers(req.headers);
  requestHeaders.set(REQUEST_ID_HEADER, requestId);
  const res = NextResponse.next({ request: { headers: requestHeaders } });
  res.headers.set(REQUEST_ID_HEADER, requestId);
  return res;
});

export const config = {
  matcher: [
    '/((?!_next/static|_next/image|favicon.ico|.*\\.(?:svg|png|jpg|jpeg|gif|webp|woff|woff2|ttf|otf)$).*)',
  ],
};
