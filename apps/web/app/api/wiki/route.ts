import { NextResponse } from 'next/server';
import { upsertWikiPage, listWikiPages, listWikiProjects, searchWikiByFTS } from '@chemclaw2/db';
import type { WikiPageCursor } from '@chemclaw2/db';
import { embedTexts } from '../../../lib/embeddings';
import { withRoute, errorResponse } from '@/lib/api-gate';
import { SlugSchema, WikiPostBodySchema } from '@/lib/wiki-schemas';
import { UUID_RE } from '@chemclaw2/agent-tools';

export const GET = withRoute(
  { rateLimit: { key: 'wiki-read', max: 60, windowMs: 60_000 } },
  async ({ req }) => {
    const url = new URL(req.url);

    if (url.searchParams.get('projects')) {
      return NextResponse.json({ projects: await listWikiProjects() });
    }

    const q = url.searchParams.get('q');
    if (q) {
      if (q.length > 500) return errorResponse('Query too long', 400);
      return NextResponse.json(await searchWikiByFTS(q));
    }

    const cursorParam = url.searchParams.get('cursor');
    let cursor: WikiPageCursor | undefined;
    if (cursorParam) {
      const sep = cursorParam.lastIndexOf('_');
      if (sep === -1) return errorResponse('Invalid cursor', 400);
      const ts = Date.parse(cursorParam.slice(0, sep));
      if (isNaN(ts)) return errorResponse('Invalid cursor', 400);
      const idPart = cursorParam.slice(sep + 1);
      if (!UUID_RE.test(idPart)) return errorResponse('Invalid cursor', 400);
      cursor = { updatedAt: new Date(ts), id: idPart };
    }
    const project = url.searchParams.get('project') ?? undefined;
    const includeArchived = url.searchParams.get('include_archived') === '1';
    const pages = await listWikiPages(50, cursor, { project, includeArchived });
    const last = pages.length === 50 ? pages[pages.length - 1] : null;
    const nextCursor = last ? `${last.updatedAt.toISOString()}_${last.id}` : null;
    return NextResponse.json({ pages, nextCursor });
  },
);

export const POST = withRoute(
  { rateLimit: { key: 'wiki', max: 20, windowMs: 60_000 }, body: WikiPostBodySchema },
  async ({ userId, body }) => {
    if (!SlugSchema.safeParse(body.slug).success) {
      return errorResponse(
        'Invalid slug: use lowercase letters, numbers, and hyphens only',
        400,
      );
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
  },
);
