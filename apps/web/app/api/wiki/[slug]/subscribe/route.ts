import { auth } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { getWikiPage, subscribeToWikiPage, unsubscribeFromWikiPage } from '@chemclaw2/db';
import { rateLimit } from '@/lib/rate-limit';

const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export async function POST(_req: Request, { params }: { params: Promise<{ slug: string }> }) {
  const { userId } = await auth();
  if (!userId) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const { limited } = await rateLimit(`wiki:${userId}`, 20, 60_000);
  if (limited) return NextResponse.json({ error: 'Too many requests' }, { status: 429 });

  const { slug } = await params;
  if (!SLUG_RE.test(slug)) return NextResponse.json({ error: 'Invalid slug' }, { status: 400 });
  const page = await getWikiPage(slug);
  if (!page) return NextResponse.json({ error: 'Not found' }, { status: 404 });

  await subscribeToWikiPage(userId, page.id);
  return NextResponse.json({ subscribed: true });
}

export async function DELETE(_req: Request, { params }: { params: Promise<{ slug: string }> }) {
  const { userId } = await auth();
  if (!userId) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const { limited } = await rateLimit(`wiki:${userId}`, 20, 60_000);
  if (limited) return NextResponse.json({ error: 'Too many requests' }, { status: 429 });

  const { slug } = await params;
  if (!SLUG_RE.test(slug)) return NextResponse.json({ error: 'Invalid slug' }, { status: 400 });
  const page = await getWikiPage(slug);
  if (!page) return NextResponse.json({ error: 'Not found' }, { status: 404 });

  await unsubscribeFromWikiPage(userId, page.id);
  return NextResponse.json({ subscribed: false });
}
