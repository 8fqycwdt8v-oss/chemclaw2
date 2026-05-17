import { auth, currentUser } from '@clerk/nextjs/server';
import { NextRequest, NextResponse } from 'next/server';
import { z } from 'zod';
import { rateLimit } from './rate-limit';

/**
 * Single response envelope for all API errors. Body shape is `{ error }` plus
 * any structured fields the caller passes through `extras`. Routes that need
 * to surface override hints or other context attach them via `extras` rather
 * than building bespoke envelopes.
 */
export function errorResponse(
  message: string,
  status: number,
  extras?: Record<string, unknown>,
  headers?: HeadersInit,
): NextResponse {
  return NextResponse.json({ error: message, ...(extras ?? {}) }, { status, headers });
}

/** Map Zod issue → HTTP status. 413 for size caps; 400 otherwise. */
function zodStatus(issue: z.ZodIssue | undefined): number {
  if (!issue) return 400;
  if (issue.code === 'too_big') {
    const path = issue.path.join('.');
    if (path === 'contentText' || path === 'content' || issue.message === 'prompt too large') {
      return 413;
    }
  }
  return 400;
}

export function zodErrorResponse(err: z.ZodError): NextResponse {
  const first = err.issues[0];
  return errorResponse(first?.message ?? 'invalid request body', zodStatus(first));
}

export async function requireUserWithRateLimit(
  key: string,
  max: number,
  windowMs: number,
  rateLimitedMessage = 'Too many requests',
): Promise<{ userId: string } | NextResponse> {
  const { userId } = await auth();
  if (!userId) return errorResponse('Unauthorized', 401);
  const { limited } = await rateLimit(`${key}:${userId}`, max, windowMs);
  if (limited) {
    return errorResponse(rateLimitedMessage, 429, undefined, { 'Retry-After': '60' });
  }
  return { userId };
}

export async function requireAdminApi(): Promise<NextResponse | { userId: string }> {
  const { userId } = await auth();
  if (!userId) return errorResponse('Unauthorized', 401);
  const user = await currentUser();
  const role = (user?.publicMetadata as { role?: string } | undefined)?.role;
  if (role !== 'admin') return errorResponse('Forbidden — admin role required', 403);
  return { userId };
}

type RouteAuth = 'user' | 'admin' | 'optional';

export type RouteConfig<S extends z.ZodTypeAny | undefined> = {
  /** Auth gate. `user` requires Clerk userId; `admin` requires role=admin; `optional` allows unauthenticated. */
  auth?: RouteAuth;
  /** Rate-limit bucket; required when auth is `user` or `admin`. */
  rateLimit?: { key: string; max: number; windowMs: number; message?: string };
  /** Zod schema for the JSON body. Omit on GET/DELETE handlers that don't read a body. */
  body?: S;
  /** Custom message when the rate-limit gate trips. */
  rateLimitedMessage?: string;
};

export type RouteContext<S extends z.ZodTypeAny | undefined> = {
  userId: string;
  body: S extends z.ZodTypeAny ? z.infer<S> : undefined;
  req: NextRequest;
};

/**
 * One wrapper that handles: auth, rate-limit, JSON parsing, Zod body validation,
 * and uniform error responses. Replaces the three patterns the codebase grew
 * (api-gate helper, inline auth+rateLimit, ad-hoc validation) with a single
 * call site. Routes that need richer error envelopes return a NextResponse
 * directly from the handler.
 *
 * For dynamic-route handlers that take `{ params }`, use `withRouteParams`.
 */
export function withRoute<S extends z.ZodTypeAny | undefined = undefined>(
  config: RouteConfig<S>,
  handler: (ctx: RouteContext<S>) => Promise<NextResponse | Response>,
): (req: NextRequest) => Promise<NextResponse | Response> {
  return async (req: NextRequest) => {
    const { ctx, error } = await runGate(req, config);
    if (error) return error;
    return handler(ctx as RouteContext<S>);
  };
}

export type ParamRouteContext<S extends z.ZodTypeAny | undefined, P> = RouteContext<S> & {
  params: P;
};

export function withRouteParams<P, S extends z.ZodTypeAny | undefined = undefined>(
  config: RouteConfig<S>,
  handler: (ctx: ParamRouteContext<S, P>) => Promise<NextResponse | Response>,
): (req: NextRequest, args: { params: Promise<P> }) => Promise<NextResponse | Response> {
  return async (req: NextRequest, args: { params: Promise<P> }) => {
    const { ctx, error } = await runGate(req, config);
    if (error) return error;
    const params = await args.params;
    return handler({ ...(ctx as RouteContext<S>), params });
  };
}

async function runGate<S extends z.ZodTypeAny | undefined>(
  req: NextRequest,
  config: RouteConfig<S>,
): Promise<{ ctx: RouteContext<S>; error: null } | { ctx: null; error: NextResponse }> {
  const authMode: RouteAuth = config.auth ?? 'user';
  let userId = '';

  if (authMode === 'admin') {
    const gate = await requireAdminApi();
    if (gate instanceof NextResponse) return { ctx: null, error: gate };
    userId = gate.userId;
  } else if (authMode === 'user') {
    if (!config.rateLimit) {
      throw new Error('withRoute: auth=user requires a rateLimit config');
    }
    const gate = await requireUserWithRateLimit(
      config.rateLimit.key,
      config.rateLimit.max,
      config.rateLimit.windowMs,
      config.rateLimit.message ?? config.rateLimitedMessage,
    );
    if (gate instanceof NextResponse) return { ctx: null, error: gate };
    userId = gate.userId;
  } else {
    const { userId: maybeId } = await auth();
    userId = maybeId ?? '';
  }

  // Admin gate handles its own rate-limit semantics today (no per-admin limiter
  // in the legacy code). When `rateLimit` is supplied alongside auth=admin we
  // apply it after the role check so a non-admin still fails fast on 401/403.
  if (authMode === 'admin' && config.rateLimit) {
    const { limited } = await rateLimit(
      `${config.rateLimit.key}:${userId}`,
      config.rateLimit.max,
      config.rateLimit.windowMs,
    );
    if (limited) {
      return {
        ctx: null,
        error: errorResponse(
          config.rateLimit.message ?? config.rateLimitedMessage ?? 'Too many requests',
          429,
          undefined,
          { 'Retry-After': '60' },
        ),
      };
    }
  }

  let body: unknown = undefined;
  if (config.body) {
    let raw: unknown;
    try {
      raw = await req.json();
    } catch {
      return { ctx: null, error: errorResponse('Invalid JSON', 400) };
    }
    const parsed = config.body.safeParse(raw);
    if (!parsed.success) {
      return { ctx: null, error: zodErrorResponse(parsed.error) };
    }
    body = parsed.data;
  }

  return {
    ctx: { userId, body, req } as RouteContext<S>,
    error: null,
  };
}
