import { currentUser } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import {
  getWikiPage, getWikiPageCitations, upsertWikiPage, updateWikiMetadata, pointInTimeWiki,
} from '@chemclaw2/db';
import { embedTexts } from '../../../../lib/embeddings';
import { requireUserWithRateLimit } from '@/lib/api-gate';
import { SlugSchema, WikiPutBodySchema, WikiPatchBodySchema, zodErrorResponse } from '@/lib/wiki-schemas';
import { withApiContext } from '@/lib/api-context';
import { logger } from '@chemclaw2/observability';

export async function GET(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
) {
  return withApiContext(async () => {
    const gate = await requireUserWithRateLimit('wiki-read', 60, 60_000);
    if (gate instanceof NextResponse) return gate;

    const { slug } = await params;
    if (!SlugSchema.safeParse(slug).success) {
      logger.info('validation_rejected', { route: 'wiki_slug', field: 'slug', reason: 'shape' });
      return NextResponse.json({ error: 'Invalid slug' }, { status: 400 });
    }

    const asOfRaw = new URL(req.url).searchParams.get('asOf');
    if (asOfRaw !== null) {
      const asOf = new Date(asOfRaw);
      if (isNaN(asOf.getTime())) {
        logger.info('validation_rejected', { route: 'wiki_slug', field: 'asOf', reason: 'bad_iso' });
        return NextResponse.json({ error: 'asOf must be an ISO-8601 timestamp' }, { status: 400 });
      }
      const snapshot = await pointInTimeWiki(slug, asOf).catch((err) => {
        logger.error('point_in_time_wiki_failed', { slug, as_of: asOf.toISOString() }, err);
        throw err;
      });
      if (!snapshot) return NextResponse.json({ error: 'Not found' }, { status: 404 });
      return NextResponse.json(snapshot);
    }

    const page = await getWikiPage(slug).catch((err) => {
      logger.error('get_wiki_page_failed', { slug }, err);
      throw err;
    });
    if (!page) return NextResponse.json({ error: 'Not found' }, { status: 404 });
    return NextResponse.json(page);
  });
}

export async function PUT(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
) {
  return withApiContext(async () => {
    const gate = await requireUserWithRateLimit('wiki', 20, 60_000);
    if (gate instanceof NextResponse) return gate;
    const { userId } = gate;

    const { slug } = await params;
    if (!SlugSchema.safeParse(slug).success) {
      logger.info('validation_rejected', { route: 'wiki_slug', field: 'slug', reason: 'shape' });
      return NextResponse.json({ error: 'Invalid slug' }, { status: 400 });
    }

    let raw: unknown;
    try {
      raw = await req.json();
    } catch (err) {
      logger.warn('json_parse_failed', { route: 'wiki_slug', method: 'PUT' }, err);
      return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
    }

    const parsed = WikiPutBodySchema.safeParse(raw);
    if (!parsed.success) {
      const { message, status } = zodErrorResponse(parsed.error);
      logger.info('validation_rejected', { route: 'wiki_slug', method: 'PUT', reason: message });
      return NextResponse.json({ error: message }, { status });
    }
    const body = parsed.data;

    const existing = await getWikiPage(slug).catch((err) => {
      logger.error('get_wiki_page_failed', { slug, op: 'put_check' }, err);
      throw err;
    });
    if (!existing) return NextResponse.json({ error: 'Not found' }, { status: 404 });

    // Direct overwrite requires page creator or admin. Non-owners must go
    // through the propose/apply review queue so wholesale rewrites are
    // auditable. PATCH already enforces this for lifecycle fields; PUT was
    // the wider gap.
    if (existing.createdBy !== userId) {
      const user = await currentUser();
      const role = (user?.publicMetadata as { role?: string } | undefined)?.role;
      if (role !== 'admin') {
        logger.info('wiki_put_forbidden', { slug, user_id: userId, creator: existing.createdBy });
        return NextResponse.json(
          { error: 'Forbidden — use propose-edit; direct overwrite requires page ownership or admin role' },
          { status: 403 },
        );
      }
    }

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
    ).catch((err) => {
      logger.error('upsert_wiki_page_failed', { slug, user_id: userId }, err);
      throw err;
    });

    return NextResponse.json({ id });
  });
}

export async function PATCH(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
) {
  return withApiContext(async () => {
    const gate = await requireUserWithRateLimit('wiki', 20, 60_000);
    if (gate instanceof NextResponse) return gate;
    const { userId } = gate;

    const { slug } = await params;
    if (!SlugSchema.safeParse(slug).success) {
      logger.info('validation_rejected', { route: 'wiki_slug', field: 'slug', reason: 'shape' });
      return NextResponse.json({ error: 'Invalid slug' }, { status: 400 });
    }

    let raw: unknown;
    try {
      raw = await req.json();
    } catch (err) {
      logger.warn('json_parse_failed', { route: 'wiki_slug', method: 'PATCH' }, err);
      return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
    }

    const parsed = WikiPatchBodySchema.safeParse(raw);
    if (!parsed.success) {
      const { message, status } = zodErrorResponse(parsed.error);
      logger.info('validation_rejected', { route: 'wiki_slug', method: 'PATCH', reason: message });
      return NextResponse.json({ error: message }, { status });
    }
    const patch = parsed.data;

    const isLifecycleEdit =
      patch.archived !== undefined ||
      patch.maturity !== undefined ||
      patch.project !== undefined;
    if (isLifecycleEdit) {
      const existing = await getWikiPage(slug).catch((err) => {
        logger.error('get_wiki_page_failed', { slug, op: 'patch_ownership_check' }, err);
        throw err;
      });
      if (!existing) return NextResponse.json({ error: 'Not found' }, { status: 404 });
      const user = await currentUser();
      const role = (user?.publicMetadata as { role?: string } | undefined)?.role;
      if (existing.createdBy !== userId && role !== 'admin') {
        logger.warn('wiki_lifecycle_forbidden', { slug, user_id: userId, owner: existing.createdBy, role: role ?? 'none' });
        return NextResponse.json(
          { error: 'Forbidden — lifecycle changes require page ownership or admin role' },
          { status: 403 },
        );
      }
    }

    const { found } = await updateWikiMetadata(slug, userId, patch).catch((err) => {
      logger.error('update_wiki_metadata_failed', { slug, user_id: userId, patch_keys: Object.keys(patch) }, err);
      throw err;
    });
    if (!found) return NextResponse.json({ error: 'Not found' }, { status: 404 });
    return NextResponse.json({ ok: true });
  });
}
