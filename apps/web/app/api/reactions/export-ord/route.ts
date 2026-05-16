import { UUID_RE } from '@/lib/validation';
import { auth } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { exportReactionsAsOrd } from '@chemclaw2/agent-tools';
import { rateLimit } from '@/lib/rate-limit';


export async function POST(req: Request) {
  const { userId } = await auth();
  if (!userId) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const { limited } = await rateLimit(`export:${userId}`, 30, 60_000);
  if (limited) return NextResponse.json({ error: 'Too many requests' }, { status: 429 });

  let body: { ids?: unknown };
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
  }
  if (!Array.isArray(body.ids) || body.ids.length === 0 || body.ids.length > 100) {
    return NextResponse.json({ error: 'ids must be a 1-100 element array of UUIDs' }, { status: 400 });
  }
  if (!body.ids.every((id) => typeof id === 'string' && UUID_RE.test(id))) {
    return NextResponse.json({ error: 'every id must be a UUID string' }, { status: 400 });
  }

  try {
    const reactions = await exportReactionsAsOrd(body.ids as string[]);
    return NextResponse.json({ reactions });
  } catch (err) {
    return NextResponse.json({ error: (err as Error).message }, { status: 400 });
  }
}
