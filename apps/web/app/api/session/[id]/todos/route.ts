import { auth } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { listSessionTodos, setTodoStatus } from '@chemclaw2/db';
import { rateLimit } from '@/lib/rate-limit';

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * v2.1-B2: read the todo list a research workflow generated for this session,
 * and let the chat UI toggle individual items.
 *
 *   GET   /api/session/<id>/todos              → list todos for the signed-in user
 *   PATCH /api/session/<id>/todos { id, status } → set a single todo's status
 */
export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { userId } = await auth();
  if (!userId) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const { id } = await params;
  if (!UUID_RE.test(id)) return NextResponse.json({ error: 'sessionId must be a UUID' }, { status: 400 });

  const { limited } = await rateLimit(`todos-read:${userId}`, 120, 60_000);
  if (limited) return NextResponse.json({ error: 'Too many requests' }, { status: 429 });

  const todos = await listSessionTodos(id, userId);
  return NextResponse.json({ todos });
}

export async function PATCH(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { userId } = await auth();
  if (!userId) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const { id } = await params;
  if (!UUID_RE.test(id)) return NextResponse.json({ error: 'sessionId must be a UUID' }, { status: 400 });

  const { limited } = await rateLimit(`todos-write:${userId}`, 60, 60_000);
  if (limited) return NextResponse.json({ error: 'Too many requests' }, { status: 429 });

  let body: { id?: unknown; status?: unknown };
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
  }
  if (typeof body.id !== 'string' || !UUID_RE.test(body.id)) {
    return NextResponse.json({ error: 'id must be a UUID' }, { status: 400 });
  }
  if (body.status !== 'pending' && body.status !== 'done') {
    return NextResponse.json({ error: 'status must be pending|done' }, { status: 400 });
  }

  const { found } = await setTodoStatus(body.id, userId, body.status);
  if (!found) return NextResponse.json({ error: 'Not found' }, { status: 404 });
  return NextResponse.json({ ok: true });
}
