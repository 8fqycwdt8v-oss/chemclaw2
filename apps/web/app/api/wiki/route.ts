import { auth } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { upsertWikiPage, listWikiPages, searchWikiByFTS } from '@chemclaw2/db';
import { embedTexts } from '../../../lib/embeddings';

const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;

export async function GET(req: Request) {
  const { userId } = await auth();
  if (!userId) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const url = new URL(req.url);
  const q = url.searchParams.get('q');
  if (q) {
    if (q.length > 500) return NextResponse.json({ error: 'Query too long' }, { status: 400 });
    const results = await searchWikiByFTS(q);
    return NextResponse.json(results);
  }
  const pages = await listWikiPages();
  return NextResponse.json(pages);
}

export async function POST(req: Request) {
  const { userId } = await auth();
  if (!userId) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const body = await req.json() as {
    slug: string;
    title: string;
    content: Record<string, unknown>;
    contentText: string;
    citations?: Array<{ citationId: string; sourceType: string; sourceId?: string; label: string }>;
  };

  if (!body.slug || !body.title) {
    return NextResponse.json({ error: 'slug and title are required' }, { status: 400 });
  }
  if (!SLUG_RE.test(body.slug) || body.slug.length > 200) {
    return NextResponse.json({ error: 'Invalid slug: use lowercase letters, numbers, and hyphens only' }, { status: 400 });
  }

  const id = await upsertWikiPage(
    body.slug,
    body.title,
    body.content ?? {},
    body.contentText ?? '',
    userId,
    body.citations ?? [],
    embedTexts,
  );

  return NextResponse.json({ id });
}
