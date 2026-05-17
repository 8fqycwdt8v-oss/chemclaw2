import { NextResponse } from 'next/server';
import { getWikiPage, subscribeToWikiPage, unsubscribeFromWikiPage } from '@chemclaw2/db';
import { requireUserWithRateLimit } from '@/lib/api-gate';
import { isValidSlug } from '@/lib/validation';

export async function POST(_req: Request, { params }: { params: Promise<{ slug: string }> }) {
  const gate = await requireUserWithRateLimit('wiki', 20, 60_000);
  if (gate instanceof NextResponse) return gate;
  const { userId } = gate;

  const { slug } = await params;
  if (!isValidSlug(slug)) return NextResponse.json({ error: 'Invalid slug' }, { status: 400 });
  const page = await getWikiPage(slug);
  if (!page) return NextResponse.json({ error: 'Not found' }, { status: 404 });

  await subscribeToWikiPage(userId, page.id);
  return NextResponse.json({ subscribed: true });
}

export async function DELETE(_req: Request, { params }: { params: Promise<{ slug: string }> }) {
  const gate = await requireUserWithRateLimit('wiki', 20, 60_000);
  if (gate instanceof NextResponse) return gate;
  const { userId } = gate;

  const { slug } = await params;
  if (!isValidSlug(slug)) return NextResponse.json({ error: 'Invalid slug' }, { status: 400 });
  const page = await getWikiPage(slug);
  if (!page) return NextResponse.json({ error: 'Not found' }, { status: 404 });

  await unsubscribeFromWikiPage(userId, page.id);
  return NextResponse.json({ subscribed: false });
}
