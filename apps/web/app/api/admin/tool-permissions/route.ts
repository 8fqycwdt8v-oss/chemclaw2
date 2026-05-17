import { z } from 'zod';
import { NextResponse } from 'next/server';
import { setToolPermission } from '@chemclaw2/db';
import { withRoute, errorResponse } from '@/lib/api-gate';

/**
 * Set or update a per-tool permission. Admin-only (Clerk publicMetadata.role
 * must be 'admin'). No standalone UI in v2 — operators curl this route or use
 * a one-shot SQL script.
 *
 * Body: { scope: 'user'|'project'|'org', scopeId, toolName, mode: 'allow'|'ask'|'deny' }
 */
const ToolPermBody = z.object({
  scope: z.enum(['user', 'project', 'org'], { message: 'scope must be user|project|org' }),
  scopeId: z.string().min(1).max(200, 'scopeId must be a non-empty string'),
  toolName: z.string().min(1).max(100, 'toolName must be a non-empty string'),
  mode: z.enum(['allow', 'ask', 'deny'], { message: 'mode must be allow|ask|deny' }),
});

export const POST = withRoute(
  { auth: 'admin', body: ToolPermBody },
  async ({ userId, body }) => {
    // Validate scopeId shape against scope. Misconfigured rows (e.g.
    // scope='project' paired with a Clerk user id) would never resolve.
    const shapeError = validateScopeShape(body.scope, body.scopeId);
    if (shapeError) return errorResponse(shapeError, 400);

    await setToolPermission(body.scope, body.scopeId, body.toolName, body.mode, userId);
    return NextResponse.json({ ok: true });
  },
);

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
