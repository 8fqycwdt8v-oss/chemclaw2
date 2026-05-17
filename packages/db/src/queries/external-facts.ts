import { sql, eq, and, desc } from 'drizzle-orm';
import { logger } from '@chemclaw2/observability';
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
 *
 * Wave-3f: caller-facing variant. Throws on DB error so test paths can
 * assert. Production tool sites use `recordExternalFactSafe` below.
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

/**
 * Wave-3f cut: persistence wrappers in eln-fetch / web-search / doc-fetch
 * all did `.catch(err => console.error(...))` with three slightly-different
 * messages. One helper, one message format, one place to change the policy.
 *
 * Returns { ok, error? } so callers can distinguish a persisted fact from a
 * logged failure — earlier versions returned `Promise<void>` on both paths,
 * which meant a missing fact was indistinguishable from a recorded one.
 */
export async function recordExternalFactSafe(
  sourceType: ExternalFactSource,
  sourceId: string,
  payload: unknown,
  fetchedBy: string,
  contentText?: string | null,
): Promise<{ ok: boolean; error?: string }> {
  try {
    await recordExternalFact(sourceType, sourceId, payload, fetchedBy, contentText);
    return { ok: true };
  } catch (err) {
    logger.error('external_facts_upsert_failed', { source_type: sourceType, source_id: sourceId }, err);
    return { ok: false, error: err instanceof Error ? err.message : String(err) };
  }
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
