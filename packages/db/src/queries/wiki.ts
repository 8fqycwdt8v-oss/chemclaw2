import { sql, eq, lt, desc, or, and } from 'drizzle-orm';
import { db } from '../client';
import { wikiPages, wikiChunks, wikiCitations, wikiSubscriptions } from '../schema/wiki';

// Split text into semantically coherent chunks for embedding.
// Strategy (in order of preference):
//   1. Paragraph boundaries (\n\n) — best context for chemistry prose
//   2. Sentence boundaries (. ! ? followed by whitespace) — for long paragraphs
//   3. Word boundaries — final fallback for run-on sentences that exceed maxSize
// Note: the sentence splitter fires on abbreviations like "Dr." and "Fig." —
// short resulting fragments are discarded by the length > 10 guard.
function chunkText(text: string, maxSize = 400, overlap = 80): string[] {
  const paragraphs = text.split(/\n{2,}/).map((p) => p.trim()).filter((p) => p.length > 0);
  const result: string[] = [];

  for (const para of paragraphs) {
    if (para.length <= maxSize) {
      if (para.length > 10) result.push(para);
      continue;
    }
    // Paragraph too long — split on sentence boundaries
    const sentences = para.split(/(?<=[.!?])\s+/).filter((s) => s.length > 0);
    let current = '';
    for (const sentence of sentences) {
      if ((current + ' ' + sentence).trim().length <= maxSize) {
        current = current ? current + ' ' + sentence : sentence;
      } else {
        if (current.length > 10) result.push(current.trim());
        const overlapText = current.length > overlap ? current.slice(-overlap) : current;
        // The reassigned current may itself exceed maxSize when sentence is very long.
        // The word-boundary fallback below handles this via flushed.length > maxSize.
        current = overlapText + ' ' + sentence;
      }
    }
    // Word-boundary fallback: if a single sentence exceeds maxSize (e.g. run-on
    // chemistry text with no sentence-ending punctuation), split on words.
    // A single token longer than maxSize is emitted as-is — not possible in natural text.
    const flushed = current.trim();
    if (flushed.length > maxSize) {
      const words = flushed.split(/\s+/);
      let sub = '';
      for (const word of words) {
        if ((sub + ' ' + word).trim().length <= maxSize) {
          sub = sub ? sub + ' ' + word : word;
        } else {
          if (sub.length > 10) result.push(sub.trim());
          sub = word;
        }
      }
      if (sub.trim().length > 10) result.push(sub.trim());
    } else if (flushed.length > 10) {
      result.push(flushed);
    }
  }

  return result;
}

/**
 * Upsert a wiki page: page row, chunk embeddings, citations.
 *
 * Embeddings are computed BEFORE opening the transaction so the OpenAI
 * round-trip does not hold a Postgres connection open. The transaction
 * covers only the fast DB writes (upsert page, replace chunks, replace citations).
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
): Promise<string> {
  // Compute embeddings outside the transaction to avoid holding a connection
  // during the network round-trip to the embedding API.
  const chunks = chunkText(contentText);
  const embeddings = chunks.length > 0 ? await embedFn(chunks) : [];
  if (embeddings.length !== chunks.length) {
    throw new Error(`embedFn returned ${embeddings.length} vectors for ${chunks.length} chunks`);
  }

  return db.transaction(async (tx) => {
    const [page] = await tx
      .insert(wikiPages)
      .values({ slug, title, content, contentText, createdBy, updatedBy: createdBy })
      .onConflictDoUpdate({
        target: wikiPages.slug,
        set: { title, content, contentText, updatedBy: createdBy, updatedAt: sql`now()` },
      })
      .returning({ id: wikiPages.id });

    await tx.delete(wikiChunks).where(eq(wikiChunks.pageId, page.id));
    if (chunks.length > 0) {
      await tx.insert(wikiChunks).values(
        chunks.map((text, i) => ({ pageId: page.id, chunkIdx: i, text, embedding: embeddings[i] })),
      );
    }

    await tx.delete(wikiCitations).where(eq(wikiCitations.pageId, page.id));
    if (citations.length > 0) {
      await tx.insert(wikiCitations).values(citations.map((c) => ({ ...c, pageId: page.id })));
    }

    return page.id;
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
 * Reproduce a wiki page as of a given timestamp by walking wiki_revisions.
 * Returns null when no revision exists for the slug at or before asof.
 * Uses the existing 0012 wiki_revisions table — no new infra.
 */
export async function pointInTimeWiki(slug: string, asof: Date) {
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
    WHERE p.slug = ${slug} AND r.updated_at <= ${asof.toISOString()}::timestamptz
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

export async function searchWikiByFTS(query: string, limit = 20) {
  return db
    .select({ id: wikiPages.id, slug: wikiPages.slug, title: wikiPages.title, contentText: wikiPages.contentText })
    .from(wikiPages)
    .where(sql`to_tsvector('english', coalesce(content_text, '')) @@ plainto_tsquery('english', ${query})`)
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

export async function semanticSearchWiki(embedding: number[], limit = 5) {
  if (embedding.length !== 1536) {
    throw new Error(`embedding must have 1536 dimensions, got ${embedding.length}`);
  }
  if (embedding.some((v) => !Number.isFinite(v))) {
    throw new Error('embedding contains non-finite values');
  }
  // Use sql.param() to ensure the vector literal is a bound parameter, not raw SQL.
  // Drizzle already parameterizes template interpolations, but this is explicit.
  const vecStr = `[${embedding.join(',')}]`;
  const distExpr = sql<number>`embedding <=> ${sql.param(vecStr)}::vector(1536)`;

  return db
    .select({
      pageId: wikiChunks.pageId,
      text: wikiChunks.text,
      distance: distExpr,
    })
    .from(wikiChunks)
    .where(sql`embedding IS NOT NULL`)
    .orderBy(distExpr)
    .limit(Math.min(limit, 50));
}
