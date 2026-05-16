import { sql, eq, lt, desc, or, and, type SQL } from 'drizzle-orm';
import { trace } from '@opentelemetry/api';
import { db } from '../client';
import { wikiPages, wikiChunks, wikiCitations, wikiSubscriptions } from '../schema/wiki';

const tracer = trace.getTracer('@chemclaw2/db');

// Split text into semantically coherent chunks for embedding.
// Strategy (in order of preference):
//   1. Paragraph boundaries (\n\n) — best context for chemistry prose
//   2. Sentence boundaries (. ! ? followed by whitespace) — for long paragraphs
//   3. Word boundaries — final fallback for run-on sentences that exceed maxSize
//
// 1200/200 is sized so chunks carry enough surrounding context for retrieval
// (chemistry text is dense; 400-char chunks under-utilize the embedding model).
// Adjacent chunks share an `overlap`-char prefix taken from the previous chunk
// so queries that straddle a paragraph boundary still hit at least one chunk.
//
// Note: the sentence splitter fires on abbreviations like "Dr." and "Fig." —
// short resulting fragments are discarded by the length > 10 guard.
export function chunkText(text: string, maxSize = 1200, overlap = 200): string[] {
  const paragraphs = text.split(/\n{2,}/).map((p) => p.trim()).filter((p) => p.length > 0);
  const result: string[] = [];

  const pushWithOverlap = (chunk: string, prependPrevTail: boolean) => {
    const t = chunk.trim();
    if (t.length <= 10) return;
    if (prependPrevTail && result.length > 0) {
      const prev = result[result.length - 1];
      const tail = prev.length > overlap ? prev.slice(-overlap) : prev;
      result.push(`${tail} ${t}`);
    } else {
      result.push(t);
    }
  };

  for (const para of paragraphs) {
    if (para.length <= maxSize) {
      pushWithOverlap(para, true);
      continue;
    }
    // Paragraph too long — split on sentence boundaries
    const sentences = para.split(/(?<=[.!?])\s+/).filter((s) => s.length > 0);
    let current = '';
    let firstInPara = true;
    for (const sentence of sentences) {
      if ((current + ' ' + sentence).trim().length <= maxSize) {
        current = current ? current + ' ' + sentence : sentence;
      } else {
        if (current.length > 10) {
          // First sub-chunk of a long paragraph inherits the previous paragraph's
          // tail; subsequent sub-chunks already include intra-paragraph overlap
          // via the `current = overlapText + sentence` reassignment below.
          pushWithOverlap(current.trim(), firstInPara);
          firstInPara = false;
        }
        const overlapText = current.length > overlap ? current.slice(-overlap) : current;
        current = overlapText + ' ' + sentence;
      }
    }
    // Word-boundary fallback: if a single sentence exceeds maxSize (e.g. run-on
    // chemistry text with no sentence-ending punctuation), split on words.
    const flushed = current.trim();
    if (flushed.length > maxSize) {
      const words = flushed.split(/\s+/);
      let sub = '';
      for (const word of words) {
        if ((sub + ' ' + word).trim().length <= maxSize) {
          sub = sub ? sub + ' ' + word : word;
        } else {
          if (sub.length > 10) {
            pushWithOverlap(sub.trim(), firstInPara);
            firstInPara = false;
          }
          sub = word;
        }
      }
      if (sub.trim().length > 10) {
        pushWithOverlap(sub.trim(), firstInPara);
      }
    } else if (flushed.length > 10) {
      pushWithOverlap(flushed, firstInPara);
    }
  }

  return result;
}

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
 * Upsert a wiki page: page row, chunk embeddings, citations.
 *
 * Embeddings are computed BEFORE opening the transaction so the OpenAI
 * round-trip does not hold a Postgres connection open. The transaction
 * covers only the fast DB writes (upsert page, replace chunks, replace citations).
 *
 * The pre-flight read of existing `content_text` lets us skip the chunk
 * delete-insert + embedding API call when the body hasn't changed. Title-only
 * edits, metadata-only writes, and idempotent retries all become near-free.
 * Citations are always replaced — they can change independently of body text.
 *
 * Concurrency note: the pre-flight read is outside the transaction. If a
 * concurrent writer commits between our read and our transaction, we may
 * either re-embed unnecessarily (benign) or skip re-embed while writing the
 * same content back (also benign — both writers agree on content). This is
 * the same race tolerated by every prior version of this function.
 *
 * embedFn receives all chunks in one call so callers can batch the API request.
 */
export async function upsertWikiPage(
  slug: string,
  title: string,
  content: Record<string, unknown>,
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

      // Pre-flight: skip embedding work when content_text is unchanged.
      // Concurrency note: the read is outside the transaction; a concurrent
      // writer landing between read and transaction may cause a benign re-embed
      // or a benign re-write of identical content. Same race as every prior
      // version of this function.
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

export async function getWikiPageCitations(pageId: string) {
  return db
    .select({
      citationId: wikiCitations.citationId,
      sourceType: wikiCitations.sourceType,
      sourceId: wikiCitations.sourceId,
      label: wikiCitations.label,
      disputed: wikiCitations.disputed,
    })
    .from(wikiCitations)
    .where(eq(wikiCitations.pageId, pageId));
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
 * list page. Cheap because wiki_pages.project is indexed (migration 0013).
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
 * Mark a single citation as disputed (or undisputed). The citation row remains —
 * audit + reader trace are preserved; UI strikes it through.
 */
export async function setCitationDisputed(
  pageId: string,
  citationId: string,
  disputed: boolean,
): Promise<{ found: boolean }> {
  const rows = await db
    .update(wikiCitations)
    .set({ disputed })
    .where(and(eq(wikiCitations.pageId, pageId), eq(wikiCitations.citationId, citationId)))
    .returning({ id: wikiCitations.id });
  return { found: rows.length > 0 };
}

/**
 * Reproduce a wiki page as of a given timestamp.
 *
 * The snapshot trigger only fires on UPDATE (migration 0012), so a page that
 * was created and never edited has zero revision rows. In that case fall back
 * to the current wiki_pages row if it predates `asof`. Returns null when the
 * slug didn't exist at the asof timestamp.
 */
export async function pointInTimeWiki(slug: string, asof: Date) {
  const asofIso = asof.toISOString();
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
  if (rows[0]) return rows[0];

  // No revision before asof — fall back to the current row if it was created
  // before asof and has never been edited (i.e., still matches the original).
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
    WHERE slug = ${slug} AND created_at <= ${asofIso}::timestamptz
    LIMIT 1
  `);
  return current[0] ?? null;
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
 * - archived pages excluded by default (deprecated content stays out of results)
 * - distance threshold: chunks beyond maxDistance are dropped (irrelevant matches
 *   would otherwise surface as "top results" for queries the wiki doesn't cover)
 * - per-page cap: at most maxChunksPerPage chunks from a single page (a long
 *   page can otherwise fill all slots, starving the agent of complementary sources)
 *
 * Over-fetches by 4x to leave headroom for the per-page cap.
 */
export async function semanticSearchWiki(
  embedding: number[],
  limit = 5,
  opts: SemanticSearchOptions = {},
): Promise<SemanticSearchResult[]> {
  if (embedding.length !== 1536) {
    throw new Error(`embedding must have 1536 dimensions, got ${embedding.length}`);
  }
  if (embedding.some((v) => !Number.isFinite(v))) {
    throw new Error('embedding contains non-finite values');
  }
  const safeLimit = Math.min(Math.max(1, limit), 50);
  const maxDistance = opts.maxDistance ?? 0.5;
  const maxChunksPerPage = Math.max(1, opts.maxChunksPerPage ?? 2);
  const includeArchived = opts.includeArchived ?? false;

  const vecStr = `[${embedding.join(',')}]`;
  const distExpr = sql<number>`wiki_chunks.embedding <=> ${sql.param(vecStr)}::vector(1536)`;

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
