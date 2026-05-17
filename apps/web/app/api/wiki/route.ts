import { auth } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { upsertWikiPage, listWikiPages, listWikiProjects, searchWikiByFTS } from '@chemclaw2/db';
import type { WikiPageCursor } from '@chemclaw2/db';
import { embedTexts } from '../../../lib/embeddings';
import { rateLimit } from '@/lib/rate-limit';
import { isValidSlug, isValidTiptapDoc } from '@/lib/validation';
import { withApiContext } from '@/lib/api-context';
import { logger } from '@chemclaw2/observability';
import {
  MAX_TITLE_LEN, MAX_MARKDOWN_LEN as MAX_CONTENT_TEXT_LEN, MAX_CITATIONS,
} from '@chemclaw2/agent-tools';

const MAX_CITATION_FIELD_LEN = 1_000;

export async function GET(req: Request) {
  return withApiContext(async () => {
    const { userId } = await auth();
    if (!userId) {
      logger.info('auth_denied', { route: 'wiki', method: 'GET' });
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const { limited } = await rateLimit(`wiki-read:${userId}`, 60, 60_000);
    if (limited) {
      logger.warn('rate_limit_hit', { route: 'wiki', method: 'GET', user_id: userId });
      return NextResponse.json({ error: 'Too many requests' }, { status: 429, headers: { 'Retry-After': '60' } });
    }

    const url = new URL(req.url);

    // GET /api/wiki?projects=1 → distinct project tags for the filter chips
    if (url.searchParams.get('projects')) {
      const projects = await listWikiProjects().catch((err) => {
        logger.error('list_wiki_projects_failed', {}, err);
        throw err;
      });
      return NextResponse.json({ projects });
    }

    const q = url.searchParams.get('q');
    if (q) {
      if (q.length > 500) {
        logger.info('validation_rejected', { route: 'wiki', field: 'q', reason: 'oversize', length: q.length });
        return NextResponse.json({ error: 'Query too long' }, { status: 400 });
      }
      const results = await searchWikiByFTS(q).catch((err) => {
        logger.error('search_wiki_fts_failed', { route: 'wiki', q_len: q.length }, err);
        throw err;
      });
      return NextResponse.json(results);
    }

    // Cursor-based pagination: ?cursor=<ISO-8601 updatedAt>_<page-uuid>
    // Composite (updatedAt, id) cursor prevents skipping pages with identical timestamps.
    // nextCursor is null when fewer than 50 pages are returned (end of results).
    const cursorParam = url.searchParams.get('cursor');
    let cursor: WikiPageCursor | undefined;
    if (cursorParam) {
      const sep = cursorParam.lastIndexOf('_');
      if (sep === -1) {
        logger.info('validation_rejected', { route: 'wiki', field: 'cursor', reason: 'no_separator' });
        return NextResponse.json({ error: 'Invalid cursor' }, { status: 400 });
      }
      const ts = Date.parse(cursorParam.slice(0, sep));
      if (isNaN(ts)) {
        logger.info('validation_rejected', { route: 'wiki', field: 'cursor', reason: 'bad_timestamp' });
        return NextResponse.json({ error: 'Invalid cursor' }, { status: 400 });
      }
      const idPart = cursorParam.slice(sep + 1);
      if (!/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(idPart)) {
        logger.info('validation_rejected', { route: 'wiki', field: 'cursor', reason: 'bad_uuid' });
        return NextResponse.json({ error: 'Invalid cursor' }, { status: 400 });
      }
      cursor = { updatedAt: new Date(ts), id: idPart };
    }
    const project = url.searchParams.get('project') ?? undefined;
    const includeArchived = url.searchParams.get('include_archived') === '1';
    const pages = await listWikiPages(50, cursor, { project, includeArchived }).catch((err) => {
      logger.error('list_wiki_pages_failed', { project, include_archived: includeArchived }, err);
      throw err;
    });
    const last = pages.length === 50 ? pages[pages.length - 1] : null;
    const nextCursor = last ? `${last.updatedAt.toISOString()}_${last.id}` : null;
    return NextResponse.json({ pages, nextCursor });
  });
}

export async function POST(req: Request) {
  return withApiContext(async () => {
    const { userId } = await auth();
    if (!userId) {
      logger.info('auth_denied', { route: 'wiki', method: 'POST' });
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const { limited } = await rateLimit(`wiki:${userId}`, 20, 60_000);
    if (limited) {
      logger.warn('rate_limit_hit', { route: 'wiki', method: 'POST', user_id: userId });
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
    } catch (err) {
      logger.warn('json_parse_failed', { route: 'wiki' }, err);
      return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
    }

    if (typeof body.slug !== 'string' || typeof body.title !== 'string' || !body.slug || !body.title) {
      logger.info('validation_rejected', { route: 'wiki', field: 'slug_or_title', reason: 'missing' });
      return NextResponse.json({ error: 'slug and title are required strings' }, { status: 400 });
    }
    if (!isValidSlug(body.slug)) {
      logger.info('validation_rejected', { route: 'wiki', field: 'slug', reason: 'shape' });
      return NextResponse.json({ error: 'Invalid slug: use lowercase letters, numbers, and hyphens only' }, { status: 400 });
    }
    if (body.title.length > MAX_TITLE_LEN) {
      logger.info('validation_rejected', { route: 'wiki', field: 'title', reason: 'oversize', length: body.title.length });
      return NextResponse.json({ error: 'title too long' }, { status: 400 });
    }
    if (body.contentText !== undefined && typeof body.contentText !== 'string') {
      logger.info('validation_rejected', { route: 'wiki', field: 'contentText', reason: 'type' });
      return NextResponse.json({ error: 'contentText must be a string' }, { status: 400 });
    }
    if (typeof body.contentText === 'string' && body.contentText.length > MAX_CONTENT_TEXT_LEN) {
      logger.info('validation_rejected', { route: 'wiki', field: 'contentText', reason: 'oversize', length: body.contentText.length });
      return NextResponse.json({ error: 'contentText too large' }, { status: 413 });
    }
    if (Array.isArray(body.citations)) {
      if (body.citations.length > MAX_CITATIONS) {
        logger.info('validation_rejected', { route: 'wiki', field: 'citations', reason: 'too_many' });
        return NextResponse.json({ error: 'too many citations' }, { status: 400 });
      }
      for (const c of body.citations) {
        if (
          typeof c.citationId !== 'string' || c.citationId.length > MAX_CITATION_FIELD_LEN ||
          typeof c.sourceType !== 'string' || c.sourceType.length > MAX_CITATION_FIELD_LEN ||
          typeof c.label !== 'string' || c.label.length > MAX_CITATION_FIELD_LEN ||
          (c.sourceId !== undefined && (typeof c.sourceId !== 'string' || c.sourceId.length > MAX_CITATION_FIELD_LEN))
        ) {
          logger.info('validation_rejected', { route: 'wiki', field: 'citations[]', reason: 'shape' });
          return NextResponse.json({ error: 'invalid citation fields' }, { status: 400 });
        }
      }
    }

    // M5: reject obviously malformed Tiptap docs so the editor doesn't crash on next load.
    if (body.content !== undefined && !isValidTiptapDoc(body.content)) {
      logger.info('validation_rejected', { route: 'wiki', field: 'content', reason: 'invalid_tiptap' });
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
    ).catch((err) => {
      logger.error('upsert_wiki_page_failed', { slug: body.slug, user_id: userId, content_len: body.contentText?.length ?? 0 }, err);
      throw err;
    });

    return NextResponse.json({ id });
  });
}
