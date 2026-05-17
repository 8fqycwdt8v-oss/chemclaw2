import { UUID_RE } from '@/lib/validation';
import { auth } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { listSessionTodos, setTodoStatus } from '@chemclaw2/db';
import { rateLimit } from '@/lib/rate-limit';
import { withApiContext } from '@/lib/api-context';
import { logger } from '@chemclaw2/observability';


/**
 * v2.1-B2: read the todo list a research workflow generated for this session,
 * and let the chat UI toggle individual items.
 *
 *   GET   /api/session/<id>/todos              → list todos for the signed-in user
 *   PATCH /api/session/<id>/todos { id, status } → set a single todo's status
 */
export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  return withApiContext(async () => {
    const { userId } = await auth();
    if (!userId) {
      logger.info('auth_denied', { route: 'todos', method: 'GET' });
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const { id } = await params;
    if (!UUID_RE.test(id)) {
      logger.info('validation_rejected', { route: 'todos', field: 'sessionId', reason: 'shape' });
      return NextResponse.json({ error: 'sessionId must be a UUID' }, { status: 400 });
    }

    const { limited } = await rateLimit(`todos-read:${userId}`, 120, 60_000);
    if (limited) {
      logger.warn('rate_limit_hit', { route: 'todos', method: 'GET', user_id: userId });
      return NextResponse.json({ error: 'Too many requests' }, { status: 429 });
    }

    const todos = await listSessionTodos(id, userId).catch((err) => {
      logger.error('list_session_todos_failed', { session_id: id, user_id: userId }, err);
      throw err;
    });
    return NextResponse.json({ todos });
  });
}

export async function PATCH(req: Request, { params }: { params: Promise<{ id: string }> }) {
  return withApiContext(async () => {
    const { userId } = await auth();
    if (!userId) {
      logger.info('auth_denied', { route: 'todos', method: 'PATCH' });
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const { id } = await params;
    if (!UUID_RE.test(id)) {
      logger.info('validation_rejected', { route: 'todos', field: 'sessionId', reason: 'shape' });
      return NextResponse.json({ error: 'sessionId must be a UUID' }, { status: 400 });
    }

    const { limited } = await rateLimit(`todos-write:${userId}`, 60, 60_000);
    if (limited) {
      logger.warn('rate_limit_hit', { route: 'todos', method: 'PATCH', user_id: userId });
      return NextResponse.json({ error: 'Too many requests' }, { status: 429 });
    }

    let body: { id?: unknown; status?: unknown };
    try {
      body = (await req.json()) as typeof body;
    } catch (err) {
      logger.warn('json_parse_failed', { route: 'todos' }, err);
      return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
    }
    if (typeof body.id !== 'string' || !UUID_RE.test(body.id)) {
      logger.info('validation_rejected', { route: 'todos', field: 'id', reason: 'shape' });
      return NextResponse.json({ error: 'id must be a UUID' }, { status: 400 });
    }
    if (body.status !== 'pending' && body.status !== 'done') {
      logger.info('validation_rejected', { route: 'todos', field: 'status', reason: 'enum' });
      return NextResponse.json({ error: 'status must be pending|done' }, { status: 400 });
    }

    const { found } = await setTodoStatus(body.id, userId, body.status).catch((err) => {
      logger.error('set_todo_status_failed', { todo_id: body.id as string, user_id: userId, status: body.status }, err);
      throw err;
    });
    if (!found) return NextResponse.json({ error: 'Not found' }, { status: 404 });
    return NextResponse.json({ ok: true });
  });
}
