import { z } from 'zod';
import { NextResponse } from 'next/server';
import { listSessionTodos, setTodoStatus } from '@chemclaw2/db';
import { withRouteParams, errorResponse } from '@/lib/api-gate';
import { UUID_RE } from '@/lib/validation';

const TodosPatchBody = z.object({
  id: z.string().refine((s) => UUID_RE.test(s), 'id must be a UUID'),
  status: z.enum(['pending', 'done'], { message: 'status must be pending|done' }),
});

export const GET = withRouteParams<{ id: string }>(
  { rateLimit: { key: 'todos-read', max: 120, windowMs: 60_000 } },
  async ({ userId, params }) => {
    if (!UUID_RE.test(params.id)) return errorResponse('sessionId must be a UUID', 400);
    const todos = await listSessionTodos(params.id, userId);
    return NextResponse.json({ todos });
  },
);

export const PATCH = withRouteParams<{ id: string }, typeof TodosPatchBody>(
  { rateLimit: { key: 'todos-write', max: 60, windowMs: 60_000 }, body: TodosPatchBody },
  async ({ userId, params, body }) => {
    if (!UUID_RE.test(params.id)) return errorResponse('sessionId must be a UUID', 400);
    const { found } = await setTodoStatus(body.id, userId, body.status);
    if (!found) return errorResponse('Not found', 404);
    return NextResponse.json({ ok: true });
  },
);
