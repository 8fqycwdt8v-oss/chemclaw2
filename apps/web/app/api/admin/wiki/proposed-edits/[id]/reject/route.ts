import { auth, currentUser } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { markProposedEditRejected } from '@chemclaw2/db';

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

/**
 * Wave-3c admin endpoint — reject a pending proposal with a required
 * reviewer comment so the audit trail captures why the change was declined.
 */
export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { userId } = await auth();
  if (!userId) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const user = await currentUser();
  const role = (user?.publicMetadata as { role?: string } | undefined)?.role;
  if (role !== 'admin') {
    return NextResponse.json({ error: 'Forbidden — admin role required' }, { status: 403 });
  }

  const { id } = await params;
  if (!UUID_RE.test(id)) return NextResponse.json({ error: 'id must be a UUID' }, { status: 400 });

  let body: { comment?: unknown };
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
  }
  if (typeof body.comment !== 'string' || body.comment.length === 0 || body.comment.length > 2000) {
    return NextResponse.json({ error: 'comment (1-2000 chars) is required' }, { status: 400 });
  }

  const { found } = await markProposedEditRejected(id, userId, body.comment);
  if (!found) {
    return NextResponse.json({ error: 'Not found or already reviewed' }, { status: 404 });
  }
  return NextResponse.json({ ok: true });
}
