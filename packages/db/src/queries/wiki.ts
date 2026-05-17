import { sql, eq, lt, desc, or, and } from 'drizzle-orm';
import { trace } from '@opentelemetry/api';
import { db } from '../client';
import { wikiPages, wikiChunks, wikiCitations, wikiSubscriptions } from '../schema/wiki';
import { wikiTables } from '../schema/wiki-tables';
import { extractMarkdownTables } from './wiki-tables';
import { chunkText } from './wiki-chunks';

const tracer = trace.getTracer('@chemclaw2/db');

// Re-export so existing call sites that imported from `./wiki` keep working.
export { chunkText } from './wiki-chunks';
export {
  getWikiPageCitations,
  setCitationDisputed,
  getCitationPair,
  findChunksContainingCitationMarker,
  recordContradiction,
} from './wiki-citations';
export {
  searchWikiByFTS,
  listWikiRevisions,
  semanticSearchWiki,
  type SemanticSearchOptions,
  type SemanticSearchResult,
} from './wiki-search';

/**
 * Optional metadata applied at upsert time. Distinct from PATCH-only fields
 * because agent writes set these on creation (needs_review=true for agent-
 * authored pages; project tag from the agent's tool input).
 */
export type UpsertWikiMetadata = {
  project?: string;
  needsReview?: boolean;
};

/**
 * Upsert a wiki page: page row, chunk embeddings, tables, citations.
 *
 * Embeddings are computed BEFORE opening the transaction so the OpenAI
 * round-trip does not hold a Postgres connection open. The transaction
 * covers only the fast DB writes.
 *
 * The pre-flight read of existing `content_text` skips the chunk
 * delete-insert + embedding API call when the body hasn't changed. Title-
 * only edits, metadata-only writes, and idempotent retries become near-free.
 * Citations are always replaced — they can change independently of body text.
 *
 * Concurrency note: the pre-flight read is outside the transaction. A
 * concurrent writer landing between read and transaction may cause a benign
 * re-embed or a benign re-write of identical content.
 */
export async function upsertWikiPage(
  slug: string,
  title: string,
  // Stored as JSONB; shape is validated at the agent-tool seam via
  // isValidTiptapDoc. Accept `unknown` here so factories don't need
  // double-casts at the call site.
  content: unknown,
  contentText: string,
  createdBy: string,
  citations: Array<{ citationId: string; sourceType: string; sourceId?: string; label: string }>,
  embedFn: (texts: string[]) => Promise<number[][]>,
  metadata: UpsertWikiMetadata = {},
): Promise<string> {
  return tracer.startActiveSpan('wiki.upsert', async (span) => {
    try {
      span.setAttribute('wiki.slug', slug);
      span.setAttribute('wiki.content_text.length', contentText.length);
      span.setAttribute('wiki.citations.count', citations.length);

      const [existing] = await db
        .select({ contentText: wikiPages.contentText })
        .from(wikiPages)
        .where(eq(wikiPages.slug, slug))
        .limit(1);
      const contentChanged = !existing || existing.contentText !== contentText;
      span.setAttribute('wiki.content_changed', contentChanged);

      let chunks: string[] = [];
      let embeddings: number[][] = [];
      if (contentChanged) {
        chunks = chunkText(contentText);
        embeddings = chunks.length > 0 ? await embedFn(chunks) : [];
        if (embeddings.length !== chunks.length) {
          throw new Error(`embedFn returned ${embeddings.length} vectors for ${chunks.length} chunks`);
        }
      }
      span.setAttribute('wiki.chunks.count', chunks.length);

      const insertValues = {
        slug,
        title,
        content,
        contentText,
        createdBy,
        updatedBy: createdBy,
        ...(metadata.project !== undefined ? { project: metadata.project } : {}),
        ...(metadata.needsReview !== undefined ? { needsReview: metadata.needsReview } : {}),
      };
      const updateSet = {
        title,
        content,
        contentText,
        updatedBy: createdBy,
        updatedAt: sql`now()`,
        ...(metadata.project !== undefined ? { project: metadata.project } : {}),
        ...(metadata.needsReview !== undefined ? { needsReview: metadata.needsReview } : {}),
      };

      // Extract markdown tables from the body. Only re-run on content change;
      // the rows mirror page state.
      const extractedTables = contentChanged ? extractMarkdownTables(contentText) : [];

      const pageId = await db.transaction(async (tx) => {
        const [page] = await tx
          .insert(wikiPages)
          .values(insertValues)
          .onConflictDoUpdate({ target: wikiPages.slug, set: updateSet })
          .returning({ id: wikiPages.id });

        if (contentChanged) {
          await tx.delete(wikiChunks).where(eq(wikiChunks.pageId, page.id));
          if (chunks.length > 0) {
            await tx.insert(wikiChunks).values(
              chunks.map((text, i) => ({ pageId: page.id, chunkIdx: i, text, embedding: embeddings[i] })),
            );
          }
          await tx.delete(wikiTables).where(eq(wikiTables.pageId, page.id));
          if (extractedTables.length > 0) {
            await tx.insert(wikiTables).values(extractedTables.map((t) => ({
              pageId: page.id,
              position: t.position,
              anchor: t.anchor ?? null,
              headers: t.headers,
              rows: t.rows,
            })));
          }
        }

        // Citations are always replaced — they can change even when content didn't.
        await tx.delete(wikiCitations).where(eq(wikiCitations.pageId, page.id));
        if (citations.length > 0) {
          await tx.insert(wikiCitations).values(citations.map((c) => ({ ...c, pageId: page.id })));
        }

        return page.id;
      });
      span.setAttribute('wiki.page_id', pageId);
      return pageId;
    } finally {
      span.end();
    }
  });
}

export async function getWikiPage(slug: string) {
  const [page] = await db.select().from(wikiPages).where(eq(wikiPages.slug, slug));
  return page ?? null;
}

// Cursor shape for listWikiPages pagination: encodes (updatedAt, id) so pages
// with identical timestamps are never skipped.
export type WikiPageCursor = { updatedAt: Date; id: string };

export type WikiListFilters = {
  project?: string;
  includeArchived?: boolean;
};

export async function listWikiPages(limit = 50, cursor?: WikiPageCursor, filters?: WikiListFilters) {
  const safeLimit = Math.min(limit, 200);
  const predicates = [];
  if (!filters?.includeArchived) predicates.push(eq(wikiPages.archived, false));
  if (filters?.project) predicates.push(eq(wikiPages.project, filters.project));
  if (cursor) {
    predicates.push(
      or(
        lt(wikiPages.updatedAt, cursor.updatedAt),
        and(eq(wikiPages.updatedAt, cursor.updatedAt), lt(wikiPages.id, cursor.id)),
      )!,
    );
  }
  return db
    .select({
      id: wikiPages.id,
      slug: wikiPages.slug,
      title: wikiPages.title,
      updatedAt: wikiPages.updatedAt,
      maturity: wikiPages.maturity,
      needsReview: wikiPages.needsReview,
      archived: wikiPages.archived,
      project: wikiPages.project,
    })
    .from(wikiPages)
    .where(predicates.length === 0 ? undefined : and(...predicates))
    .orderBy(desc(wikiPages.updatedAt), desc(wikiPages.id))
    .limit(safeLimit);
}

/**
 * Distinct project tags across non-archived wiki pages, for filter chips in the
 * list page. Cheap because wiki_pages.project is indexed.
 */
export async function listWikiProjects(): Promise<string[]> {
  const rows = await db.execute<{ project: string }>(
    sql`SELECT DISTINCT project FROM wiki_pages WHERE project IS NOT NULL AND archived = false ORDER BY project`,
  );
  return rows.map((r) => r.project);
}

/**
 * Update lifecycle metadata in one shot: needsReview / archived / maturity / project.
 * Returns whether the page existed.
 */
export async function updateWikiMetadata(
  slug: string,
  updatedBy: string,
  patch: { needsReview?: boolean; archived?: boolean; maturity?: string; project?: string | null },
): Promise<{ found: boolean }> {
  if (patch.maturity && !['exploratory', 'validated', 'authoritative'].includes(patch.maturity)) {
    throw new Error(`invalid maturity: ${patch.maturity}`);
  }
  const set: Record<string, unknown> = { updatedBy };
  if (patch.needsReview !== undefined) set.needsReview = patch.needsReview;
  if (patch.archived !== undefined) set.archived = patch.archived;
  if (patch.maturity !== undefined) set.maturity = patch.maturity;
  if (patch.project !== undefined) set.project = patch.project;
  const rows = await db
    .update(wikiPages)
    .set(set)
    .where(eq(wikiPages.slug, slug))
    .returning({ id: wikiPages.id });
  return { found: rows.length > 0 };
}

/**
 * Reproduce a wiki page as of a given timestamp.
 *
 * The snapshot trigger fires on UPDATE and writes the PRE-edit content into
 * wiki_revisions stamped with the moment that version became current. Check
 * the current row first: if its `updated_at <= asof`, return it. Otherwise
 * look up the latest revision that does. Pages that didn't exist at `asof`
 * return null.
 */
export async function pointInTimeWiki(slug: string, asof: Date) {
  const asofIso = asof.toISOString();
  const current = await db.execute<{
    title: string;
    content: unknown;
    content_text: string | null;
    version: number;
    updated_at: string;
    updated_by: string | null;
  }>(sql`
    SELECT title, content, content_text, version, updated_at, updated_by
    FROM wiki_pages
    WHERE slug = ${slug} AND updated_at <= ${asofIso}::timestamptz
    LIMIT 1
  `);
  if (current[0]) return current[0];

  const rows = await db.execute<{
    title: string;
    content: unknown;
    content_text: string | null;
    version: number;
    updated_at: string;
    updated_by: string | null;
  }>(sql`
    SELECT r.title, r.content, r.content_text, r.version, r.updated_at, r.updated_by
    FROM wiki_revisions r
    JOIN wiki_pages p ON p.id = r.page_id
    WHERE p.slug = ${slug} AND r.updated_at <= ${asofIso}::timestamptz
    ORDER BY r.updated_at DESC
    LIMIT 1
  `);
  return rows[0] ?? null;
}

/**
 * Subscribe a user to a wiki page. Idempotent — sets last_seen_version to the
 * current version so the user only sees notifications for changes from now on.
 */
export async function subscribeToWikiPage(userId: string, pageId: string): Promise<void> {
  const [page] = await db.select({ version: wikiPages.version }).from(wikiPages).where(eq(wikiPages.id, pageId));
  if (!page) throw new Error('Page not found');
  await db
    .insert(wikiSubscriptions)
    .values({ userId, pageId, lastSeenVersion: page.version })
    .onConflictDoUpdate({
      target: [wikiSubscriptions.userId, wikiSubscriptions.pageId],
      set: { lastSeenVersion: page.version },
    });
}

export async function unsubscribeFromWikiPage(userId: string, pageId: string): Promise<void> {
  await db
    .delete(wikiSubscriptions)
    .where(and(eq(wikiSubscriptions.userId, userId), eq(wikiSubscriptions.pageId, pageId)));
}

export async function isSubscribed(userId: string, pageId: string): Promise<boolean> {
  const [row] = await db
    .select({ userId: wikiSubscriptions.userId })
    .from(wikiSubscriptions)
    .where(and(eq(wikiSubscriptions.userId, userId), eq(wikiSubscriptions.pageId, pageId)));
  return !!row;
}

/**
 * Count subscribed pages whose current version exceeds the last_seen_version.
 * Used by the nav badge — pull-on-page-load, no push channel.
 */
export async function countUnreadSubscriptions(userId: string): Promise<number> {
  const rows = await db.execute<{ count: number }>(sql`
    SELECT COUNT(*)::int AS count
    FROM wiki_subscriptions s
    JOIN wiki_pages p ON p.id = s.page_id
    WHERE s.user_id = ${userId} AND p.version > s.last_seen_version
  `);
  return rows[0]?.count ?? 0;
}
