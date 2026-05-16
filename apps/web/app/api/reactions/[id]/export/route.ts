import { auth } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { exportReactionsAsOrd } from '@chemclaw2/agent-tools';
import { rateLimit } from '@/lib/rate-limit';

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { userId } = await auth();
  if (!userId) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const { limited } = await rateLimit(`export:${userId}`, 30, 60_000);
  if (limited) return NextResponse.json({ error: 'Too many requests' }, { status: 429 });

  const { id } = await params;
  if (!UUID_RE.test(id)) return NextResponse.json({ error: 'invalid reaction id' }, { status: 400 });

  const [reaction] = await exportReactionsAsOrd([id]);
  if (!reaction) return NextResponse.json({ error: 'Reaction not found' }, { status: 404 });
  return NextResponse.json(reaction);
}
