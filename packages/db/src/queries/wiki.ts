import { sql, eq, desc } from 'drizzle-orm';
import { db } from '../client';
import { wikiPages, wikiChunks, wikiCitations } from '../schema/wiki';

// Split on whitespace boundaries to avoid mid-word cuts, with overlap
function chunkText(text: string, chunkSize = 400, overlap = 80): string[] {
  const chunks: string[] = [];
  let i = 0;
  while (i < text.length) {
    let end = Math.min(i + chunkSize, text.length);
    if (end < text.length) {
      const lastSpace = text.lastIndexOf(' ', end);
      if (lastSpace > i) end = lastSpace + 1;
    }
    const chunk = text.slice(i, end).trim();
    if (chunk.length > 10) chunks.push(chunk);
    i = Math.max(end - overlap, i + 1); // prevent infinite loop
  }
  return chunks;
}

/**
 * Upsert a wiki page atomically: page row, chunk embeddings, citations.
 * Wrapped in a transaction to prevent interleaved chunk deletes/inserts
 * from concurrent saves to the same slug.
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
  return db.transaction(async (tx) => {
    const [page] = await tx
      .insert(wikiPages)
      .values({ slug, title, content, contentText, createdBy, updatedBy: createdBy })
      .onConflictDoUpdate({
        target: wikiPages.slug,
        set: { title, content, contentText, updatedBy: createdBy },
      })
      .returning({ id: wikiPages.id });

    // Re-embed all chunks in a single batched call
    await tx.delete(wikiChunks).where(eq(wikiChunks.pageId, page.id));
    const chunks = chunkText(contentText);
    if (chunks.length > 0) {
      const embeddings = await embedFn(chunks);
      await tx.insert(wikiChunks).values(
        chunks.map((text, i) => ({ pageId: page.id, chunkIdx: i, text, embedding: embeddings[i] })),
      );
    }

    // Replace citations
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

export async function listWikiPages(limit = 50) {
  return db
    .select({ id: wikiPages.id, slug: wikiPages.slug, title: wikiPages.title, updatedAt: wikiPages.updatedAt })
    .from(wikiPages)
    .orderBy(desc(wikiPages.updatedAt))
    .limit(limit);
}

export async function searchWikiByFTS(query: string, limit = 20) {
  return db
    .select({ id: wikiPages.id, slug: wikiPages.slug, title: wikiPages.title, contentText: wikiPages.contentText })
    .from(wikiPages)
    .where(sql`to_tsvector('english', coalesce(content_text, '')) @@ plainto_tsquery('english', ${query})`)
    .limit(limit);
}

export async function semanticSearchWiki(embedding: number[], limit = 5) {
  if (embedding.length !== 1536) {
    throw new Error(`embedding must have 1536 dimensions, got ${embedding.length}`);
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
    .limit(limit);
}
