import { NextResponse } from 'next/server';
import { setToolPermission } from '@chemclaw2/db';
import { requireAdminWithRateLimit } from '@/lib/api-gate';
import { withApiContext } from '@/lib/api-context';
import { logger } from '@chemclaw2/observability';

/**
 * Set or update a per-tool permission. Admin-only (Clerk publicMetadata.role
 * must be 'admin'). No standalone UI in v2 — operators curl this route or use
 * a one-shot SQL script. A full admin surface stays deferred per the v2 plan.
 *
 * Body: { scope: 'user'|'project'|'org', scopeId, toolName, mode: 'allow'|'ask'|'deny' }
 */
export async function POST(req: Request) {
  return withApiContext(async () => {
    const gate = await requireAdminWithRateLimit('tool-permissions', 60, 60_000);
    if (gate instanceof NextResponse) return gate;
    const { userId } = gate;

    let body: { scope?: unknown; scopeId?: unknown; toolName?: unknown; mode?: unknown };
    try {
      body = (await req.json()) as typeof body;
    } catch (err) {
      logger.warn('json_parse_failed', { route: 'admin_tool_permissions' }, err);
      return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
    }
    if (body.scope !== 'user' && body.scope !== 'project' && body.scope !== 'org') {
      logger.info('validation_rejected', { route: 'admin_tool_permissions', field: 'scope', reason: 'enum' });
      return NextResponse.json({ error: 'scope must be user|project|org' }, { status: 400 });
    }
    if (typeof body.scopeId !== 'string' || body.scopeId.length === 0 || body.scopeId.length > 200) {
      logger.info('validation_rejected', { route: 'admin_tool_permissions', field: 'scopeId', reason: 'shape' });
      return NextResponse.json({ error: 'scopeId must be a non-empty string' }, { status: 400 });
    }
    if (typeof body.toolName !== 'string' || body.toolName.length === 0 || body.toolName.length > 100) {
      logger.info('validation_rejected', { route: 'admin_tool_permissions', field: 'toolName', reason: 'shape' });
      return NextResponse.json({ error: 'toolName must be a non-empty string' }, { status: 400 });
    }
    if (body.mode !== 'allow' && body.mode !== 'ask' && body.mode !== 'deny') {
      logger.info('validation_rejected', { route: 'admin_tool_permissions', field: 'mode', reason: 'enum' });
      return NextResponse.json({ error: 'mode must be allow|ask|deny' }, { status: 400 });
    }

    // v2.1-B3: validate that scopeId matches the expected shape for its scope.
    // Misconfigured rows (e.g. scope='project' paired with a Clerk user id) would
    // silently never resolve. The cheapest mitigation is rejecting them here.
    const shapeError = validateScopeShape(body.scope, body.scopeId);
    if (shapeError) {
      logger.info('validation_rejected', { route: 'admin_tool_permissions', field: 'scopeId_for_scope', reason: 'shape' });
      return NextResponse.json({ error: shapeError }, { status: 400 });
    }

    await setToolPermission(body.scope, body.scopeId, body.toolName, body.mode, userId).catch((err) => {
      logger.error('set_tool_permission_failed', { scope: body.scope, scope_id: body.scopeId, tool: body.toolName, mode: body.mode, admin_id: userId }, err);
      throw err;
    });
    return NextResponse.json({ ok: true });
  });
}

function validateScopeShape(scope: 'user' | 'project' | 'org', scopeId: string): string | null {
  if (scope === 'user') {
    if (!/^user_[A-Za-z0-9]+$/.test(scopeId)) {
      return "scopeId for scope='user' must be a Clerk user id (user_...)";
    }
    return null;
  }
  if (scope === 'project') {
    if (/^user_[A-Za-z0-9]+$/.test(scopeId)) {
      return "scopeId for scope='project' must not be a Clerk user id";
    }
    if (scopeId === 'org') {
      return "scopeId for scope='project' must not be the literal 'org'";
    }
    return null;
  }
  // scope === 'org'
  if (scopeId !== 'org') {
    return "scopeId for scope='org' must be the literal string 'org'";
  }
  return null;
}
