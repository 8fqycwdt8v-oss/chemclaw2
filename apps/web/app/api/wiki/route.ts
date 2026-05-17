import { NextResponse } from 'next/server';
import { upsertWikiPage, listWikiPages, listWikiProjects, searchWikiByFTS } from '@chemclaw2/db';
import type { WikiPageCursor } from '@chemclaw2/db';
import { embedTexts } from '../../../lib/embeddings';
import { requireUserWithRateLimit } from '@/lib/api-gate';
import { SlugSchema, WikiPostBodySchema, zodErrorResponse } from '@/lib/wiki-schemas';
import { withApiContext } from '@/lib/api-context';
import { logger } from '@chemclaw2/observability';
import { UUID_RE } from '@chemclaw2/agent-tools';

export async function GET(req: Request) {
  return withApiContext(async () => {
    const gate = await requireUserWithRateLimit('wiki-read', 60, 60_000);
    if (gate instanceof NextResponse) return gate;

    const url = new URL(req.url);

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
      if (!UUID_RE.test(idPart)) {
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
    const gate = await requireUserWithRateLimit('wiki', 20, 60_000);
    if (gate instanceof NextResponse) return gate;
    const { userId } = gate;

    let raw: unknown;
    try {
      raw = await req.json();
    } catch (err) {
      logger.warn('json_parse_failed', { route: 'wiki' }, err);
      return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
    }

    const parsed = WikiPostBodySchema.safeParse(raw);
    if (!parsed.success) {
      const { message, status } = zodErrorResponse(parsed.error);
      logger.info('validation_rejected', { route: 'wiki', reason: message });
      return NextResponse.json({ error: message }, { status });
    }
    const body = parsed.data;

    const slugCheck = SlugSchema.safeParse(body.slug);
    if (!slugCheck.success) {
      logger.info('validation_rejected', { route: 'wiki', field: 'slug', reason: 'shape' });
      return NextResponse.json({ error: 'Invalid slug: use lowercase letters, numbers, and hyphens only' }, { status: 400 });
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
