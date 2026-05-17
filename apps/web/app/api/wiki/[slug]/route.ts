import { auth, currentUser } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import {
  getWikiPage, getWikiPageCitations, upsertWikiPage, updateWikiMetadata, pointInTimeWiki,
} from '@chemclaw2/db';
import { embedTexts } from '../../../../lib/embeddings';
import { rateLimit } from '@/lib/rate-limit';
import { isValidSlug, isValidTiptapDoc } from '@/lib/validation';
import { withApiContext } from '@/lib/api-context';
import { logger } from '@chemclaw2/observability';
import {
  MAX_TITLE_LEN, MAX_MARKDOWN_LEN as MAX_CONTENT_TEXT_LEN,
  MAX_CITATIONS, MAX_PROJECT_LEN,
} from '@chemclaw2/agent-tools';

const MAX_CITATION_FIELD_LEN = 1_000;

export async function GET(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
) {
  return withApiContext(async () => {
    const { userId } = await auth();
    if (!userId) {
      logger.info('auth_denied', { route: 'wiki_slug', method: 'GET' });
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const { limited } = await rateLimit(`wiki-read:${userId}`, 60, 60_000);
    if (limited) {
      logger.warn('rate_limit_hit', { route: 'wiki_slug', method: 'GET', user_id: userId });
      return NextResponse.json({ error: 'Too many requests' }, { status: 429, headers: { 'Retry-After': '60' } });
    }

    const { slug } = await params;
    if (!isValidSlug(slug)) {
      logger.info('validation_rejected', { route: 'wiki_slug', field: 'slug', reason: 'shape' });
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
    const { userId } = await auth();
    if (!userId) {
      logger.info('auth_denied', { route: 'wiki_slug', method: 'PUT' });
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const { limited } = await rateLimit(`wiki:${userId}`, 20, 60_000);
    if (limited) {
      logger.warn('rate_limit_hit', { route: 'wiki_slug', method: 'PUT', user_id: userId });
      return NextResponse.json({ error: 'Too many requests' }, { status: 429, headers: { 'Retry-After': '60' } });
    }

    const { slug } = await params;
    if (!isValidSlug(slug)) {
      logger.info('validation_rejected', { route: 'wiki_slug', field: 'slug', reason: 'shape' });
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
    } catch (err) {
      logger.warn('json_parse_failed', { route: 'wiki_slug', method: 'PUT' }, err);
      return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
    }

    const existing = await getWikiPage(slug).catch((err) => {
      logger.error('get_wiki_page_failed', { slug, op: 'put_check' }, err);
      throw err;
    });
    if (!existing) return NextResponse.json({ error: 'Not found' }, { status: 404 });

    if (body.title !== undefined && (typeof body.title !== 'string' || body.title.length > MAX_TITLE_LEN)) {
      logger.info('validation_rejected', { route: 'wiki_slug', field: 'title', reason: 'shape' });
      return NextResponse.json({ error: 'title must be a string of at most 500 characters' }, { status: 400 });
    }
    if (body.contentText !== undefined && typeof body.contentText !== 'string') {
      logger.info('validation_rejected', { route: 'wiki_slug', field: 'contentText', reason: 'type' });
      return NextResponse.json({ error: 'contentText must be a string' }, { status: 400 });
    }
    if (typeof body.contentText === 'string' && body.contentText.length > MAX_CONTENT_TEXT_LEN) {
      logger.info('validation_rejected', { route: 'wiki_slug', field: 'contentText', reason: 'oversize', length: body.contentText.length });
      return NextResponse.json({ error: 'contentText too large' }, { status: 413 });
    }
    if (Array.isArray(body.citations)) {
      if (body.citations.length > MAX_CITATIONS) {
        logger.info('validation_rejected', { route: 'wiki_slug', field: 'citations', reason: 'too_many' });
        return NextResponse.json({ error: 'too many citations' }, { status: 400 });
      }
      for (const c of body.citations) {
        if (
          typeof c.citationId !== 'string' || c.citationId.length > MAX_CITATION_FIELD_LEN ||
          typeof c.sourceType !== 'string' || c.sourceType.length > MAX_CITATION_FIELD_LEN ||
          typeof c.label !== 'string' || c.label.length > MAX_CITATION_FIELD_LEN ||
          (c.sourceId !== undefined && (typeof c.sourceId !== 'string' || c.sourceId.length > MAX_CITATION_FIELD_LEN))
        ) {
          logger.info('validation_rejected', { route: 'wiki_slug', field: 'citations[]', reason: 'shape' });
          return NextResponse.json({ error: 'invalid citation fields' }, { status: 400 });
        }
      }
    }

    // M5: reject malformed Tiptap docs on update.
    if (body.content !== undefined && !isValidTiptapDoc(body.content)) {
      logger.info('validation_rejected', { route: 'wiki_slug', field: 'content', reason: 'invalid_tiptap' });
      return NextResponse.json({ error: 'content must be a Tiptap doc {type:"doc",content:[]}' }, { status: 400 });
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

const VALID_MATURITIES = new Set(['exploratory', 'validated', 'authoritative']);

export async function PATCH(
  req: Request,
  { params }: { params: Promise<{ slug: string }> },
) {
  return withApiContext(async () => {
    const { userId } = await auth();
    if (!userId) {
      logger.info('auth_denied', { route: 'wiki_slug', method: 'PATCH' });
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const { limited } = await rateLimit(`wiki:${userId}`, 20, 60_000);
    if (limited) {
      logger.warn('rate_limit_hit', { route: 'wiki_slug', method: 'PATCH', user_id: userId });
      return NextResponse.json({ error: 'Too many requests' }, { status: 429, headers: { 'Retry-After': '60' } });
    }

    const { slug } = await params;
    if (!isValidSlug(slug)) {
      logger.info('validation_rejected', { route: 'wiki_slug', field: 'slug', reason: 'shape' });
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
    } catch (err) {
      logger.warn('json_parse_failed', { route: 'wiki_slug', method: 'PATCH' }, err);
      return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
    }

    const patch: { needsReview?: boolean; archived?: boolean; maturity?: string; project?: string | null } = {};
    if (typeof body.needsReview === 'boolean') patch.needsReview = body.needsReview;
    if (typeof body.archived === 'boolean') patch.archived = body.archived;
    if (typeof body.maturity === 'string') {
      if (!VALID_MATURITIES.has(body.maturity)) {
        logger.info('validation_rejected', { route: 'wiki_slug', field: 'maturity', reason: 'enum' });
        return NextResponse.json({ error: 'invalid maturity' }, { status: 400 });
      }
      patch.maturity = body.maturity;
    }
    if (body.project === null) {
      patch.project = null;
    } else if (typeof body.project === 'string') {
      if (body.project.length > MAX_PROJECT_LEN) {
        logger.info('validation_rejected', { route: 'wiki_slug', field: 'project', reason: 'oversize', length: body.project.length });
        return NextResponse.json({ error: 'project too long' }, { status: 400 });
      }
      patch.project = body.project;
    }
    if (Object.keys(patch).length === 0) {
      logger.info('validation_rejected', { route: 'wiki_slug', field: 'body', reason: 'empty_patch' });
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
