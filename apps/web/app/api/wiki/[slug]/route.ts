import { currentUser } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import {
  getWikiPage, getWikiPageCitations, upsertWikiPage, updateWikiMetadata, pointInTimeWiki,
} from '@chemclaw2/db';
import { embedTexts } from '../../../../lib/embeddings';
import { requireUserWithRateLimit } from '@/lib/api-gate';
import { SlugSchema, WikiPutBodySchema, WikiPatchBodySchema, zodErrorResponse } from '@/lib/wiki-schemas';

export async function GET(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
) {
  const gate = await requireUserWithRateLimit('wiki-read', 60, 60_000);
  if (gate instanceof NextResponse) return gate;

  const { slug } = await params;
  if (!SlugSchema.safeParse(slug).success) {
    return NextResponse.json({ error: 'Invalid slug' }, { status: 400 });
  }

  // v2.1-B1: bi-temporal lookup. ?asOf=<ISO8601> returns the page revision
  // active at that instant via pointInTimeWiki (which reads wiki_revisions and
  // falls back to the current row when no edit predates asOf). Compliance use:
  // "what did this page say on 2026-03-01?".
  const asOfRaw = new URL(req.url).searchParams.get('asOf');
  if (asOfRaw !== null) {
    const asOf = new Date(asOfRaw);
    if (isNaN(asOf.getTime())) {
      return NextResponse.json({ error: 'asOf must be an ISO-8601 timestamp' }, { status: 400 });
    }
    const snapshot = await pointInTimeWiki(slug, asOf);
    if (!snapshot) return NextResponse.json({ error: 'Not found' }, { status: 404 });
    return NextResponse.json(snapshot);
  }

  const page = await getWikiPage(slug);
  if (!page) return NextResponse.json({ error: 'Not found' }, { status: 404 });
  return NextResponse.json(page);
}

export async function PUT(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
) {
  const gate = await requireUserWithRateLimit('wiki', 20, 60_000);
  if (gate instanceof NextResponse) return gate;
  const { userId } = gate;

  const { slug } = await params;
  if (!SlugSchema.safeParse(slug).success) {
    return NextResponse.json({ error: 'Invalid slug' }, { status: 400 });
  }

  let raw: unknown;
  try {
    raw = await req.json();
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
  }

  const parsed = WikiPutBodySchema.safeParse(raw);
  if (!parsed.success) {
    const { message, status } = zodErrorResponse(parsed.error);
    return NextResponse.json({ error: message }, { status });
  }
  const body = parsed.data;

  const existing = await getWikiPage(slug);
  if (!existing) return NextResponse.json({ error: 'Not found' }, { status: 404 });

  const citations = body.citations !== undefined
    ? body.citations
    : (await getWikiPageCitations(existing.id)).map((c) => ({ ...c, sourceId: c.sourceId ?? undefined }));

  const id = await upsertWikiPage(
    slug,
    body.title ?? existing.title,
    body.content ?? existing.content,
    body.contentText ?? existing.contentText,
    userId,
    citations,
    embedTexts,
  );

  return NextResponse.json({ id });
}

export async function PATCH(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
) {
  const gate = await requireUserWithRateLimit('wiki', 20, 60_000);
  if (gate instanceof NextResponse) return gate;
  const { userId } = gate;

  const { slug } = await params;
  if (!SlugSchema.safeParse(slug).success) {
    return NextResponse.json({ error: 'Invalid slug' }, { status: 400 });
  }

  let raw: unknown;
  try {
    raw = await req.json();
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
  }

  const parsed = WikiPatchBodySchema.safeParse(raw);
  if (!parsed.success) {
    const { message, status } = zodErrorResponse(parsed.error);
    return NextResponse.json({ error: message }, { status });
  }
  const patch = parsed.data;

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
