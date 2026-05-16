import { sql, eq, and, desc } from 'drizzle-orm';
import { db } from '../client';
import { externalFacts } from '../schema/external-facts';

export type ExternalFactSource = 'eln' | 'web_search' | 'doc' | (string & {});

export type ExternalFactRow = {
  id: string;
  sourceType: string;
  sourceId: string;
  payload: unknown;
  contentText: string | null;
  firstSeen: Date;
  lastSeen: Date;
  fetchedBy: string;
};

/**
 * Upsert a tool-fetch result keyed by (source_type, source_id). The first
 * write records first_seen; subsequent writes refresh last_seen + payload
 * + fetched_by. Designed for the eln_fetch / web_search / fetch_document
 * tools so their results survive past the originating session.
 *
 * contentText is the lossy text extract used for FTS; the canonical agent
 * view stays in payload.
 */
export async function recordExternalFact(
  sourceType: ExternalFactSource,
  sourceId: string,
  payload: unknown,
  fetchedBy: string,
  contentText?: string | null,
): Promise<void> {
  await db
    .insert(externalFacts)
    .values({
      sourceType,
      sourceId,
      payload: payload as object,
      contentText: contentText ?? null,
      fetchedBy,
    })
    .onConflictDoUpdate({
      target: [externalFacts.sourceType, externalFacts.sourceId],
      set: {
        payload: payload as object,
        contentText: contentText ?? null,
        lastSeen: sql`NOW()`,
        fetchedBy,
      },
    });
}

export async function getExternalFact(
  sourceType: ExternalFactSource,
  sourceId: string,
): Promise<ExternalFactRow | null> {
  const [row] = await db
    .select()
    .from(externalFacts)
    .where(and(eq(externalFacts.sourceType, sourceType), eq(externalFacts.sourceId, sourceId)));
  return row ?? null;
}

/**
 * Full-text search across external_facts.content_text. Returns the most
 * recently-seen matches first so stale duplicates of an unstable web search
 * fall behind a fresh re-fetch.
 */
export async function searchExternalFactsByFTS(
  query: string,
  limit = 10,
): Promise<ExternalFactRow[]> {
  const safeLimit = Math.min(Math.max(1, limit), 50);
  return db
    .select()
    .from(externalFacts)
    .where(sql`to_tsvector('english', coalesce(${externalFacts.contentText}, ''))
              @@ plainto_tsquery('english', ${query})`)
    .orderBy(desc(externalFacts.lastSeen))
    .limit(safeLimit);
}
