import { auth } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { getWikiPage, upsertWikiPage } from '@chemclaw2/db';
import { embedTexts } from '../../../../lib/embeddings';

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ slug: string }> },
) {
  const { userId } = await auth();
  if (!userId) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const { slug } = await params;
  const page = await getWikiPage(slug);
  if (!page) return NextResponse.json({ error: 'Not found' }, { status: 404 });
  return NextResponse.json(page);
}

export async function PUT(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
) {
  const { userId } = await auth();
  if (!userId) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const { slug } = await params;

  const body = await req.json() as {
    title?: string;
    content: Record<string, unknown>;
    contentText: string;
    citations?: Array<{ citationId: string; sourceType: string; sourceId?: string; label: string }>;
  };

  const existing = await getWikiPage(slug);
  if (!existing) return NextResponse.json({ error: 'Not found' }, { status: 404 });

  const id = await upsertWikiPage(
    slug,
    body.title ?? existing.title,
    body.content,
    body.contentText,
    userId,
    body.citations ?? [],
    embedTexts,
  );

  return NextResponse.json({ id });
}
