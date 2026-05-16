import Link from 'next/link';
import { UserButton } from '@clerk/nextjs';
import { auth, currentUser } from '@clerk/nextjs/server';
import { countUnreadSubscriptions, listPendingProposedEdits } from '@chemclaw2/db';

export default async function AppLayout({ children }: { children: React.ReactNode }) {
  const { userId } = await auth();
  const unread = userId ? await countUnreadSubscriptions(userId).catch(() => 0) : 0;
  // Wave-3d: admin-only review-queue link with a count badge. Skip role +
  // queue lookups for non-signed-in / non-admin users so the nav stays cheap.
  const user = userId ? await currentUser() : null;
  const isAdmin =
    (user?.publicMetadata as { role?: string } | undefined)?.role === 'admin';
  const queueCount = isAdmin
    ? await listPendingProposedEdits(50).then((p) => p.length).catch(() => 0)
    : 0;
  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b bg-white">
        <nav className="max-w-6xl mx-auto px-4 h-14 flex items-center gap-6">
          <Link href="/chat" className="font-semibold tracking-tight">ChemClaw</Link>
          <Link href="/chat" className="text-sm text-slate-700 hover:text-slate-950">Chat</Link>
          <Link href="/wiki" className="text-sm text-slate-700 hover:text-slate-950 relative">
            Wiki
            {unread > 0 && (
              <span className="absolute -top-1 -right-3 text-[10px] bg-blue-600 text-white rounded-full px-1.5 py-0.5">
                {unread}
              </span>
            )}
          </Link>
          <Link href="/search" className="text-sm text-slate-700 hover:text-slate-950">Search</Link>
          {isAdmin && (
            <Link
              href="/admin/wiki/queue"
              className="text-sm text-slate-700 hover:text-slate-950 relative"
            >
              Review
              {queueCount > 0 && (
                <span className="absolute -top-1 -right-3 text-[10px] bg-amber-600 text-white rounded-full px-1.5 py-0.5">
                  {queueCount}
                </span>
              )}
            </Link>
          )}
          <div className="ml-auto"><UserButton afterSignOutUrl="/sign-in" /></div>
        </nav>
      </header>
      <main className="flex-1 max-w-6xl w-full mx-auto px-4 py-6">{children}</main>
    </div>
  );
}
