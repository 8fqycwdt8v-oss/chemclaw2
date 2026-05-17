import { auth, currentUser } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { logger } from '@chemclaw2/observability';

/**
 * Wave-3f cut: the Clerk auth+currentUser+role triple was open-coded in 8+
 * places (admin API routes + admin server pages + layout nav badge). One
 * canonical access-control point removes drift between admin enforcement
 * surfaces.
 *
 * `requireAdminApi` is for API routes — returns a `NextResponse` to return
 * directly, or `{ userId }` on success. Callers do:
 *
 *   const gate = await requireAdminApi();
 *   if (gate instanceof NextResponse) return gate;
 *   const { userId } = gate;
 *
 * `getAdminContext` is for server pages — returns `{ userId, isAdmin }`
 * (with `userId: null` when unauthenticated) so the page can decide between
 * `redirect('/sign-in')`, rendering a 403 message, or showing admin UI.
 */
export async function requireAdminApi(): Promise<NextResponse | { userId: string }> {
  const { userId } = await auth();
  if (!userId) {
    logger.warn('admin_unauth');
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }
  const user = await currentUser();
  const role = (user?.publicMetadata as { role?: string } | undefined)?.role;
  if (role !== 'admin') {
    logger.warn('admin_forbidden', { user_id: userId, role: role ?? 'none' });
    return NextResponse.json({ error: 'Forbidden — admin role required' }, { status: 403 });
  }
  return { userId };
}

export type AdminContext = {
  userId: string | null;
  isAdmin: boolean;
};

export async function getAdminContext(): Promise<AdminContext> {
  const { userId } = await auth();
  if (!userId) return { userId: null, isAdmin: false };
  const user = await currentUser();
  const role = (user?.publicMetadata as { role?: string } | undefined)?.role;
  return { userId, isAdmin: role === 'admin' };
}
