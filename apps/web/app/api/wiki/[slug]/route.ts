import { auth, currentUser } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { getWikiPage, getWikiPageCitations, upsertWikiPage, updateWikiMetadata } from '@chemclaw2/db';
import { embedTexts } from '../../../../lib/embeddings';
import { rateLimit } from '@/lib/rate-limit';
import { isValidSlug, isValidTiptapDoc } from '@/lib/validation';

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
  if (!isValidSlug(slug)) {
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
  if (!isValidSlug(slug)) {
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

  if (body.title !== undefined && (typeof body.title !== 'string' || body.title.length > MAX_TITLE_LEN)) {
    return NextResponse.json({ error: 'title must be a string of at most 500 characters' }, { status: 400 });
  }
  if (body.contentText !== undefined && typeof body.contentText !== 'string') {
    return NextResponse.json({ error: 'contentText must be a string' }, { status: 400 });
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

  // M5: reject malformed Tiptap docs on update.
  if (body.content !== undefined && !isValidTiptapDoc(body.content)) {
    return NextResponse.json({ error: 'content must be a Tiptap doc {type:"doc",content:[]}' }, { status: 400 });
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

const VALID_MATURITIES = new Set(['exploratory', 'validated', 'authoritative']);

export async function PATCH(
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
  if (!isValidSlug(slug)) {
    return NextResponse.json({ error: 'Invalid slug' }, { status: 400 });
  }

  let body: {
    needsReview?: unknown;
    archived?: unknown;
    maturity?: unknown;
    project?: unknown;
  };
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
  }

  const patch: { needsReview?: boolean; archived?: boolean; maturity?: string; project?: string | null } = {};
  if (typeof body.needsReview === 'boolean') patch.needsReview = body.needsReview;
  if (typeof body.archived === 'boolean') patch.archived = body.archived;
  if (typeof body.maturity === 'string') {
    if (!VALID_MATURITIES.has(body.maturity)) {
      return NextResponse.json({ error: 'invalid maturity' }, { status: 400 });
    }
    patch.maturity = body.maturity;
  }
  if (body.project === null) {
    patch.project = null;
  } else if (typeof body.project === 'string') {
    if (body.project.length > 100) return NextResponse.json({ error: 'project too long' }, { status: 400 });
    patch.project = body.project;
  }
  if (Object.keys(patch).length === 0) {
    return NextResponse.json({ error: 'no metadata fields provided' }, { status: 400 });
  }

  // Followup #8: lifecycle changes (archive, maturity demotion/promotion,
  // project reassignment) are curation actions — restrict to the page's
  // original creator or an admin. needsReview alone is collaborative-OK
  // because it's the "flag for attention" affordance any chemist needs.
  const isLifecycleEdit =
    patch.archived !== undefined ||
    patch.maturity !== undefined ||
    patch.project !== undefined;
  if (isLifecycleEdit) {
    const existing = await getWikiPage(slug);
    if (!existing) return NextResponse.json({ error: 'Not found' }, { status: 404 });
    const user = await currentUser();
    const role = (user?.publicMetadata as { role?: string } | undefined)?.role;
    if (existing.createdBy !== userId && role !== 'admin') {
      return NextResponse.json(
        { error: 'Forbidden — lifecycle changes require page ownership or admin role' },
        { status: 403 },
      );
    }
  }

  const { found } = await updateWikiMetadata(slug, userId, patch);
  if (!found) return NextResponse.json({ error: 'Not found' }, { status: 404 });
  return NextResponse.json({ ok: true });
}
