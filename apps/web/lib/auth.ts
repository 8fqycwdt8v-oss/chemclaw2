import { auth, currentUser } from '@clerk/nextjs/server';

/**
 * Server-side admin context for RSC pages. API routes should use
 * `requireAdminApi` / `withRoute({ auth: 'admin' })` from `./api-gate`.
 */
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

// Re-export for callers that still import from `./auth`.
export { requireAdminApi } from './api-gate';
