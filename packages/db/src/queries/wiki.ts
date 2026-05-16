import { sql, eq, lt, desc, or, and } from 'drizzle-orm';
import { db } from '../client';
import { wikiPages, wikiChunks, wikiCitations } from '../schema/wiki';

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
    .select({ citationId: wikiCitations.citationId, sourceType: wikiCitations.sourceType, sourceId: wikiCitations.sourceId, label: wikiCitations.label })
    .from(wikiCitations)
    .where(eq(wikiCitations.pageId, pageId));
}

// Cursor shape for listWikiPages pagination: encodes (updatedAt, id) so pages
// with identical timestamps are never skipped.
export type WikiPageCursor = { updatedAt: Date; id: string };

export async function listWikiPages(limit = 50, cursor?: WikiPageCursor) {
  const safeLimit = Math.min(limit, 200);
  const base = db
    .select({ id: wikiPages.id, slug: wikiPages.slug, title: wikiPages.title, updatedAt: wikiPages.updatedAt })
    .from(wikiPages)
    .orderBy(desc(wikiPages.updatedAt), desc(wikiPages.id));
  const q = cursor
    ? base.where(
        or(
          lt(wikiPages.updatedAt, cursor.updatedAt),
          and(eq(wikiPages.updatedAt, cursor.updatedAt), lt(wikiPages.id, cursor.id)),
        ),
      )
    : base;
  return q.limit(safeLimit);
}

export async function searchWikiByFTS(query: string, limit = 20) {
  return db
    .select({ id: wikiPages.id, slug: wikiPages.slug, title: wikiPages.title, contentText: wikiPages.contentText })
    .from(wikiPages)
    .where(sql`to_tsvector('english', coalesce(content_text, '')) @@ plainto_tsquery('english', ${query})`)
    .limit(Math.min(limit, 200));
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
