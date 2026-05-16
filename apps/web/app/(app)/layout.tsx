import Link from 'next/link';
import { UserButton } from '@clerk/nextjs';

export default function AppLayout({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen flex flex-col">
      <header className="border-b bg-white">
        <nav className="max-w-6xl mx-auto px-4 h-14 flex items-center gap-6">
          <Link href="/chat" className="font-semibold tracking-tight">ChemClaw</Link>
          <Link href="/chat" className="text-sm text-slate-700 hover:text-slate-950">Chat</Link>
          <Link href="/wiki" className="text-sm text-slate-700 hover:text-slate-950">Wiki</Link>
          <Link href="/search" className="text-sm text-slate-700 hover:text-slate-950">Search</Link>
          <div className="ml-auto"><UserButton afterSignOutUrl="/sign-in" /></div>
        </nav>
      </header>
      <main className="flex-1 max-w-6xl w-full mx-auto px-4 py-6">{children}</main>
    </div>
  );
}
