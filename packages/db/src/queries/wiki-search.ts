import { sql, eq, type SQL } from 'drizzle-orm';
import { db } from '../client';
import { wikiPages, wikiChunks } from '../schema/wiki';
import { EMBED_DIM } from '../embedding-constants';

/**
 * Full-text search across non-archived wiki pages by `plainto_tsquery` over
 * the page body. Returns the same shape the wiki list page uses, so callers
 * can render the result rows without an extra DB round-trip.
 */
export async function searchWikiByFTS(query: string, limit = 20, includeArchived = false) {
  const predicates: SQL[] = [
    sql`to_tsvector('english', coalesce(content_text, '')) @@ plainto_tsquery('english', ${query})`,
  ];
  if (!includeArchived) predicates.push(sql`archived = false`);
  return db
    .select({
      id: wikiPages.id,
      slug: wikiPages.slug,
      title: wikiPages.title,
      contentText: wikiPages.contentText,
      maturity: wikiPages.maturity,
    })
    .from(wikiPages)
    .where(sql.join(predicates, sql` AND `))
    .limit(Math.min(limit, 200));
}

/**
 * Most recent revisions for a wiki page, newest first. version/updated_at/
 * updated_by only — bodies aren't replayed here; pointInTimeWiki handles that.
 */
export async function listWikiRevisions(pageId: string, limit = 10) {
  const rows = await db.execute<{ version: number; updated_at: string; updated_by: string | null }>(
    sql`SELECT version, updated_at, updated_by FROM wiki_revisions
        WHERE page_id = ${pageId}::uuid
        ORDER BY version DESC
        LIMIT ${Math.min(limit, 100)}`,
  );
  return rows.map((r) => ({ version: r.version, updatedAt: r.updated_at, updatedBy: r.updated_by }));
}

export type SemanticSearchOptions = {
  /** Maximum cosine distance to accept (0 = identical, 2 = opposite). Default 0.5. */
  maxDistance?: number;
  /** Maximum chunks returned from any single page. Default 2. */
  maxChunksPerPage?: number;
  /** Include archived pages. Default false. */
  includeArchived?: boolean;
};

export type SemanticSearchResult = {
  pageId: string;
  slug: string;
  title: string;
  maturity: string;
  text: string;
  distance: number;
};

/**
 * Vector-similarity search over wiki_chunks. JOINs wiki_pages so the caller
 * gets renderable identifiers (slug, title, maturity) without a second round-trip.
 *
 * Filters:
 * - archived pages excluded by default
 * - distance threshold drops irrelevant matches
 * - per-page cap prevents a long page from filling every slot
 *
 * Over-fetches by 4x to leave headroom for the per-page cap.
 */
export async function semanticSearchWiki(
  embedding: number[],
  limit = 5,
  opts: SemanticSearchOptions = {},
): Promise<SemanticSearchResult[]> {
  if (embedding.length !== EMBED_DIM) {
    throw new Error(`embedding must have ${EMBED_DIM} dimensions, got ${embedding.length}`);
  }
  if (embedding.some((v) => !Number.isFinite(v))) {
    throw new Error('embedding contains non-finite values');
  }
  const safeLimit = Math.min(Math.max(1, limit), 50);
  const maxDistance = opts.maxDistance ?? 0.5;
  const maxChunksPerPage = Math.max(1, opts.maxChunksPerPage ?? 2);
  const includeArchived = opts.includeArchived ?? false;

  const vecStr = `[${embedding.join(',')}]`;
  const distExpr = sql<number>`wiki_chunks.embedding <=> ${sql.param(vecStr)}::vector(${sql.raw(String(EMBED_DIM))})`;

  const predicates: SQL[] = [sql`wiki_chunks.embedding IS NOT NULL`];
  if (!includeArchived) predicates.push(sql`wiki_pages.archived = false`);

  const rows = await db
    .select({
      pageId: wikiChunks.pageId,
      slug: wikiPages.slug,
      title: wikiPages.title,
      maturity: wikiPages.maturity,
      text: wikiChunks.text,
      distance: distExpr,
    })
    .from(wikiChunks)
    .innerJoin(wikiPages, eq(wikiPages.id, wikiChunks.pageId))
    .where(sql.join(predicates, sql` AND `))
    .orderBy(distExpr)
    .limit(safeLimit * 4);

  const perPage = new Map<string, number>();
  const out: SemanticSearchResult[] = [];
  for (const r of rows) {
    if (r.distance > maxDistance) continue;
    const seen = perPage.get(r.pageId) ?? 0;
    if (seen >= maxChunksPerPage) continue;
    perPage.set(r.pageId, seen + 1);
    out.push(r);
    if (out.length >= safeLimit) break;
  }
  return out;
}
