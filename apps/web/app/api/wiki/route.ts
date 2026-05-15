import { auth } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { upsertWikiPage, listWikiPages, searchWikiByFTS } from '@chemclaw2/db';
import { embedTexts } from '../../../lib/embeddings';

export async function GET(req: Request) {
  const { userId } = await auth();
  if (!userId) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const url = new URL(req.url);
  const q = url.searchParams.get('q');
  if (q) {
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
