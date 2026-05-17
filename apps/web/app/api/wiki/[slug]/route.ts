import { currentUser } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import {
  getWikiPage, getWikiPageCitations, upsertWikiPage, updateWikiMetadata, pointInTimeWiki,
} from '@chemclaw2/db';
import { embedTexts } from '../../../../lib/embeddings';
import { withRouteParams, errorResponse } from '@/lib/api-gate';
import { SlugSchema, WikiPutBodySchema, WikiPatchBodySchema } from '@/lib/wiki-schemas';

export const GET = withRouteParams<{ slug: string }>(
  { rateLimit: { key: 'wiki-read', max: 60, windowMs: 60_000 } },
  async ({ req, params }) => {
    if (!SlugSchema.safeParse(params.slug).success) return errorResponse('Invalid slug', 400);

    const asOfRaw = new URL(req.url).searchParams.get('asOf');
    if (asOfRaw !== null) {
      const asOf = new Date(asOfRaw);
      if (isNaN(asOf.getTime())) return errorResponse('asOf must be an ISO-8601 timestamp', 400);
      const snapshot = await pointInTimeWiki(params.slug, asOf);
      if (!snapshot) return errorResponse('Not found', 404);
      return NextResponse.json(snapshot);
    }

    const page = await getWikiPage(params.slug);
    if (!page) return errorResponse('Not found', 404);
    return NextResponse.json(page);
  },
);

export const PUT = withRouteParams<{ slug: string }, typeof WikiPutBodySchema>(
  { rateLimit: { key: 'wiki', max: 20, windowMs: 60_000 }, body: WikiPutBodySchema },
  async ({ userId, params, body }) => {
    if (!SlugSchema.safeParse(params.slug).success) return errorResponse('Invalid slug', 400);

    const existing = await getWikiPage(params.slug);
    if (!existing) return errorResponse('Not found', 404);

    // Direct overwrite requires page creator or admin. Non-owners must go
    // through the propose/apply review queue so wholesale rewrites are
    // auditable. PATCH already enforces this for lifecycle fields; PUT was
    // the wider gap.
    if (existing.createdBy !== userId) {
      const user = await currentUser();
      const role = (user?.publicMetadata as { role?: string } | undefined)?.role;
      if (role !== 'admin') {
        return errorResponse(
          'Forbidden — use propose-edit; direct overwrite requires page ownership or admin role',
          403,
        );
      }
    }

    const citations = body.citations !== undefined
      ? body.citations
      : (await getWikiPageCitations(existing.id)).map((c) => ({
          ...c,
          sourceId: c.sourceId ?? undefined,
        }));

    const id = await upsertWikiPage(
      params.slug,
      body.title ?? existing.title,
      body.content ?? existing.content,
      body.contentText ?? existing.contentText,
      userId,
      citations,
      embedTexts,
    );
    return NextResponse.json({ id });
  },
);

export const PATCH = withRouteParams<{ slug: string }, typeof WikiPatchBodySchema>(
  { rateLimit: { key: 'wiki', max: 20, windowMs: 60_000 }, body: WikiPatchBodySchema },
  async ({ userId, params, body: patch }) => {
    if (!SlugSchema.safeParse(params.slug).success) return errorResponse('Invalid slug', 400);

    // Lifecycle changes (archive, maturity, project) are curation actions —
    // restrict to the page's original creator or an admin. needsReview alone
    // is collaborative-OK because it's the "flag for attention" affordance.
    const isLifecycleEdit =
      patch.archived !== undefined ||
      patch.maturity !== undefined ||
      patch.project !== undefined;
    if (isLifecycleEdit) {
      const existing = await getWikiPage(params.slug);
      if (!existing) return errorResponse('Not found', 404);
      const user = await currentUser();
      const role = (user?.publicMetadata as { role?: string } | undefined)?.role;
      if (existing.createdBy !== userId && role !== 'admin') {
        return errorResponse(
          'Forbidden — lifecycle changes require page ownership or admin role',
          403,
        );
      }
    }

    const { found } = await updateWikiMetadata(params.slug, userId, patch);
    if (!found) return errorResponse('Not found', 404);
    return NextResponse.json({ ok: true });
  },
);
