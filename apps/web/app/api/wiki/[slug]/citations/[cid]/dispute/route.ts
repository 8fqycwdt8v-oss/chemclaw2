import { auth } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { getWikiPage, setCitationDisputed } from '@chemclaw2/db';
import { rateLimit } from '@/lib/rate-limit';
import { isValidSlug } from '@/lib/validation';

export async function POST(
  req: Request,
  { params }: { params: Promise<{ slug: string; cid: string }> },
) {
  const { userId } = await auth();
  if (!userId) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const { limited } = await rateLimit(`wiki:${userId}`, 20, 60_000);
  if (limited) return NextResponse.json({ error: 'Too many requests' }, { status: 429 });

  const { slug, cid } = await params;
  if (!isValidSlug(slug) || cid.length === 0 || cid.length > 200) {
    return NextResponse.json({ error: 'Invalid slug or citation id' }, { status: 400 });
  }
  const page = await getWikiPage(slug);
  if (!page) return NextResponse.json({ error: 'Not found' }, { status: 404 });

  let body: { disputed?: unknown };
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
  }
  if (typeof body.disputed !== 'boolean') {
    return NextResponse.json({ error: 'disputed (boolean) is required' }, { status: 400 });
  }

  const { found } = await setCitationDisputed(page.id, cid, body.disputed);
  if (!found) return NextResponse.json({ error: 'Citation not found' }, { status: 404 });
  return NextResponse.json({ disputed: body.disputed });
}
