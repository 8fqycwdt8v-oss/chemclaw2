import { auth, currentUser } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { setToolPermission } from '@chemclaw2/db';

/**
 * Set or update a per-tool permission. Admin-only (Clerk publicMetadata.role
 * must be 'admin'). No standalone UI in v2 — operators curl this route or use
 * a one-shot SQL script. A full admin surface stays deferred per the v2 plan.
 *
 * Body: { scope: 'user'|'project'|'org', scopeId, toolName, mode: 'allow'|'ask'|'deny' }
 */
export async function POST(req: Request) {
  const { userId } = await auth();
  if (!userId) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const user = await currentUser();
  const role = (user?.publicMetadata as { role?: string } | undefined)?.role;
  if (role !== 'admin') return NextResponse.json({ error: 'Forbidden — admin role required' }, { status: 403 });

  let body: { scope?: unknown; scopeId?: unknown; toolName?: unknown; mode?: unknown };
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
  }
  if (body.scope !== 'user' && body.scope !== 'project' && body.scope !== 'org') {
    return NextResponse.json({ error: 'scope must be user|project|org' }, { status: 400 });
  }
  if (typeof body.scopeId !== 'string' || body.scopeId.length === 0 || body.scopeId.length > 200) {
    return NextResponse.json({ error: 'scopeId must be a non-empty string' }, { status: 400 });
  }
  if (typeof body.toolName !== 'string' || body.toolName.length === 0 || body.toolName.length > 100) {
    return NextResponse.json({ error: 'toolName must be a non-empty string' }, { status: 400 });
  }
  if (body.mode !== 'allow' && body.mode !== 'ask' && body.mode !== 'deny') {
    return NextResponse.json({ error: 'mode must be allow|ask|deny' }, { status: 400 });
  }

  await setToolPermission(body.scope, body.scopeId, body.toolName, body.mode, userId);
  return NextResponse.json({ ok: true });
}
