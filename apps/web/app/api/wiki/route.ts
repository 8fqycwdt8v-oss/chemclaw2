import { auth } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { upsertWikiPage, listWikiPages, searchWikiByFTS } from '@chemclaw2/db';
import { embedTexts } from '../../../lib/embeddings';
import { rateLimit } from '@/lib/rate-limit';

const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
const MAX_TITLE_LEN = 500;
const MAX_CONTENT_TEXT_LEN = 500_000;
const MAX_CITATIONS = 200;
const MAX_CITATION_FIELD_LEN = 1_000;

export async function GET(req: Request) {
  const { userId } = await auth();
  if (!userId) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });

  const { limited } = rateLimit(`wiki-read:${userId}`, 60, 60_000);
  if (limited) {
    return NextResponse.json({ error: 'Too many requests' }, { status: 429, headers: { 'Retry-After': '60' } });
  }

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

  const { limited } = rateLimit(`wiki:${userId}`, 20, 60_000);
  if (limited) {
    return NextResponse.json({ error: 'Too many requests' }, { status: 429, headers: { 'Retry-After': '60' } });
  }

  let body: {
    slug: string;
    title: string;
    content: Record<string, unknown>;
    contentText: string;
    citations?: Array<{ citationId: string; sourceType: string; sourceId?: string; label: string }>;
  };
  try {
    body = await req.json() as typeof body;
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
  }

  if (!body.slug || !body.title) {
    return NextResponse.json({ error: 'slug and title are required' }, { status: 400 });
  }
  if (!SLUG_RE.test(body.slug) || body.slug.length > 200) {
    return NextResponse.json({ error: 'Invalid slug: use lowercase letters, numbers, and hyphens only' }, { status: 400 });
  }
  if (body.title.length > MAX_TITLE_LEN) {
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
