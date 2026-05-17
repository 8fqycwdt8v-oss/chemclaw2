import { NextResponse } from 'next/server';
import { upsertWikiPage, listWikiPages, listWikiProjects, searchWikiByFTS } from '@chemclaw2/db';
import type { WikiPageCursor } from '@chemclaw2/db';
import { embedTexts } from '../../../lib/embeddings';
import { requireUserWithRateLimit } from '@/lib/api-gate';
import { isValidSlug, isValidTiptapDoc, validateCitations } from '@/lib/validation';
import {
  MAX_TITLE_LEN, MAX_MARKDOWN_LEN as MAX_CONTENT_TEXT_LEN,
} from '@chemclaw2/agent-tools';

export async function GET(req: Request) {
  const gate = await requireUserWithRateLimit('wiki-read', 60, 60_000);
  if (gate instanceof NextResponse) return gate;

  const url = new URL(req.url);

  // GET /api/wiki?projects=1 → distinct project tags for the filter chips
  if (url.searchParams.get('projects')) {
    return NextResponse.json({ projects: await listWikiProjects() });
  }

  const q = url.searchParams.get('q');
  if (q) {
    if (q.length > 500) return NextResponse.json({ error: 'Query too long' }, { status: 400 });
    const results = await searchWikiByFTS(q);
    return NextResponse.json(results);
  }

  // Cursor-based pagination: ?cursor=<ISO-8601 updatedAt>_<page-uuid>
  // Composite (updatedAt, id) cursor prevents skipping pages with identical timestamps.
  // nextCursor is null when fewer than 50 pages are returned (end of results).
  const cursorParam = url.searchParams.get('cursor');
  let cursor: WikiPageCursor | undefined;
  if (cursorParam) {
    const sep = cursorParam.lastIndexOf('_');
    if (sep === -1) return NextResponse.json({ error: 'Invalid cursor' }, { status: 400 });
    const ts = Date.parse(cursorParam.slice(0, sep));
    if (isNaN(ts)) return NextResponse.json({ error: 'Invalid cursor' }, { status: 400 });
    const idPart = cursorParam.slice(sep + 1);
    if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(idPart)) {
      return NextResponse.json({ error: 'Invalid cursor' }, { status: 400 });
    }
    cursor = { updatedAt: new Date(ts), id: idPart };
  }
  const project = url.searchParams.get('project') ?? undefined;
  const includeArchived = url.searchParams.get('include_archived') === '1';
  const pages = await listWikiPages(50, cursor, { project, includeArchived });
  const last = pages.length === 50 ? pages[pages.length - 1] : null;
  const nextCursor = last ? `${last.updatedAt.toISOString()}_${last.id}` : null;
  return NextResponse.json({ pages, nextCursor });
}

export async function POST(req: Request) {
  const gate = await requireUserWithRateLimit('wiki', 20, 60_000);
  if (gate instanceof NextResponse) return gate;
  const { userId } = gate;

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

  if (typeof body.slug !== 'string' || typeof body.title !== 'string' || !body.slug || !body.title) {
    return NextResponse.json({ error: 'slug and title are required strings' }, { status: 400 });
  }
  if (!isValidSlug(body.slug)) {
    return NextResponse.json({ error: 'Invalid slug: use lowercase letters, numbers, and hyphens only' }, { status: 400 });
  }
  if (body.title.length > MAX_TITLE_LEN) {
    return NextResponse.json({ error: 'title too long' }, { status: 400 });
  }
  if (body.contentText !== undefined && typeof body.contentText !== 'string') {
    return NextResponse.json({ error: 'contentText must be a string' }, { status: 400 });
  }
  if (typeof body.contentText === 'string' && body.contentText.length > MAX_CONTENT_TEXT_LEN) {
    return NextResponse.json({ error: 'contentText too large' }, { status: 413 });
  }
  const citationError = validateCitations(body.citations);
  if (citationError) return NextResponse.json({ error: citationError }, { status: 400 });

  // M5: reject obviously malformed Tiptap docs so the editor doesn't crash on next load.
  if (body.content !== undefined && !isValidTiptapDoc(body.content)) {
    return NextResponse.json({ error: 'content must be a Tiptap doc {type:"doc",content:[]}' }, { status: 400 });
  }

  const id = await upsertWikiPage(
    body.slug,
    body.title,
    body.content ?? { type: 'doc', content: [] },
    body.contentText ?? '',
    userId,
    body.citations ?? [],
    embedTexts,
  );

  return NextResponse.json({ id });
}
