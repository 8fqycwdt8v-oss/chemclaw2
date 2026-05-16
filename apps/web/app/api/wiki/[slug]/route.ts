import { auth } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { getWikiPage, getWikiPageCitations, upsertWikiPage } from '@chemclaw2/db';
import { embedTexts } from '../../../../lib/embeddings';
import { rateLimit } from '@/lib/rate-limit';

const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const MAX_TITLE_LEN = 500;
const MAX_CONTENT_TEXT_LEN = 500_000;
const MAX_CITATIONS = 200;
const MAX_CITATION_FIELD_LEN = 1_000;

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ slug: string }> },
) {
  const { userId } = await auth();
  if (!userId) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const { limited } = await rateLimit(`wiki-read:${userId}`, 60, 60_000);
  if (limited) {
    return NextResponse.json({ error: 'Too many requests' }, { status: 429, headers: { 'Retry-After': '60' } });
  }

  const { slug } = await params;
  if (!SLUG_RE.test(slug) || slug.length > 200) {
    return NextResponse.json({ error: 'Invalid slug' }, { status: 400 });
  }
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

  const { limited } = await rateLimit(`wiki:${userId}`, 20, 60_000);
  if (limited) {
    return NextResponse.json({ error: 'Too many requests' }, { status: 429, headers: { 'Retry-After': '60' } });
  }

  const { slug } = await params;
  if (!SLUG_RE.test(slug) || slug.length > 200) {
    return NextResponse.json({ error: 'Invalid slug' }, { status: 400 });
  }

  let body: {
    title?: string;
    content?: Record<string, unknown>;
    contentText?: string;
    citations?: Array<{ citationId: string; sourceType: string; sourceId?: string; label: string }>;
  };
  try {
    body = await req.json() as typeof body;
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
  }

  const existing = await getWikiPage(slug);
  if (!existing) return NextResponse.json({ error: 'Not found' }, { status: 404 });

  if (body.title && body.title.length > MAX_TITLE_LEN) {
    return NextResponse.json({ error: 'title too long' }, { status: 400 });
  }
  if (typeof body.contentText === 'string' && body.contentText.length > MAX_CONTENT_TEXT_LEN) {
    return NextResponse.json({ error: 'contentText too large' }, { status: 413 });
  }
  if (Array.isArray(body.citations)) {
    if (body.citations.length > MAX_CITATIONS) {
      return NextResponse.json({ error: 'too many citations' }, { status: 400 });
    }
    for (const c of body.citations) {
      if (
        typeof c.citationId !== 'string' || c.citationId.length > MAX_CITATION_FIELD_LEN ||
        typeof c.sourceType !== 'string' || c.sourceType.length > MAX_CITATION_FIELD_LEN ||
        typeof c.label !== 'string' || c.label.length > MAX_CITATION_FIELD_LEN ||
        (c.sourceId !== undefined && (typeof c.sourceId !== 'string' || c.sourceId.length > MAX_CITATION_FIELD_LEN))
      ) {
        return NextResponse.json({ error: 'invalid citation fields' }, { status: 400 });
      }
    }
  }

  const citations = body.citations !== undefined
    ? body.citations
    : (await getWikiPageCitations(existing.id)).map((c) => ({ ...c, sourceId: c.sourceId ?? undefined }));

  const id = await upsertWikiPage(
    slug,
    body.title ?? existing.title,
    body.content ?? existing.content as Record<string, unknown>,
    body.contentText ?? existing.contentText ?? '',
    userId,
    citations,
    embedTexts,
  );

  return NextResponse.json({ id });
}
