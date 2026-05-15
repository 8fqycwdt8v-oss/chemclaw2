import { sql, eq } from 'drizzle-orm';
import { db } from '../client';
import { wikiPages, wikiChunks, wikiCitations } from '../schema/wiki';

// Chunk text into overlapping segments
function chunkText(text: string, chunkSize = 400, overlap = 80): string[] {
  const chunks: string[] = [];
  let i = 0;
  while (i < text.length) {
    chunks.push(text.slice(i, i + chunkSize));
    i += chunkSize - overlap;
  }
  return chunks.filter((c) => c.trim().length > 0);
}

export async function upsertWikiPage(
  slug: string,
  title: string,
  content: Record<string, unknown>,
  contentText: string,
  createdBy: string,
  citations: Array<{ citationId: string; sourceType: string; sourceId?: string; label: string }>,
  embedFn: (text: string) => Promise<number[]>,
): Promise<string> {
  const [page] = await db
    .insert(wikiPages)
    .values({ slug, title, content, contentText, createdBy, updatedBy: createdBy })
    .onConflictDoUpdate({
      target: wikiPages.slug,
      set: { title, content, contentText, updatedBy: createdBy },
    })
    .returning({ id: wikiPages.id });

  // Re-embed chunks
  await db.delete(wikiChunks).where(eq(wikiChunks.pageId, page.id));
  const chunks = chunkText(contentText);
  for (let i = 0; i < chunks.length; i++) {
    const embedding = await embedFn(chunks[i]);
    await db.insert(wikiChunks).values({ pageId: page.id, chunkIdx: i, text: chunks[i], embedding });
  }

  // Upsert citations
  await db.delete(wikiCitations).where(eq(wikiCitations.pageId, page.id));
  if (citations.length > 0) {
    await db.insert(wikiCitations).values(citations.map((c) => ({ ...c, pageId: page.id })));
  }

  return page.id;
}

export async function getWikiPage(slug: string) {
  const [page] = await db.select().from(wikiPages).where(eq(wikiPages.slug, slug));
  return page ?? null;
}

export async function listWikiPages(limit = 50) {
  return db
    .select({ id: wikiPages.id, slug: wikiPages.slug, title: wikiPages.title, updatedAt: wikiPages.updatedAt })
    .from(wikiPages)
    .orderBy(sql`updated_at DESC`)
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
  const vecStr = `[${embedding.join(',')}]`;
  const rows = await db
    .select({
      pageId: wikiChunks.pageId,
      text: wikiChunks.text,
      distance: sql<number>`embedding <=> ${vecStr}::vector(1536)`,
    })
    .from(wikiChunks)
    .where(sql`embedding IS NOT NULL`)
    .orderBy(sql`embedding <=> ${vecStr}::vector(1536)`)
    .limit(limit);
  return rows;
}
