import { Suspense } from 'react';
import { ChatClient } from '@/components/chat/ChatClient';

// Chat is fully interactive and uses useSearchParams (session resume URL param).
// Skip static prerendering — there's nothing useful to cache.
export const dynamic = 'force-dynamic';

export default function ChatPage() {
  return (
    <Suspense fallback={<div className="text-slate-500 text-sm">Loading chat…</div>}>
      <ChatClient />
    </Suspense>
  );
}
