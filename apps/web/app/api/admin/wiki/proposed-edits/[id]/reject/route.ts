import { UUID_RE } from '@/lib/validation';
import { NextResponse } from 'next/server';
import { markProposedEditRejected } from '@chemclaw2/db';
import { requireAdminApi } from '@/lib/auth';

/**
 * Wave-3c admin endpoint — reject a pending proposal with a required
 * reviewer comment so the audit trail captures why the change was declined.
 */
export async function POST(req: Request, { params }: { params: Promise<{ id: string }> }) {
  const gate = await requireAdminApi();
  if (gate instanceof NextResponse) return gate;
  const { userId } = gate;

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
