import { eq, sql, desc } from 'drizzle-orm';
import { db } from '../client';
import { papers } from '../schema/papers';

export type PaperRow = {
  id: string;
  doi: string | null;
  pubmedId: string | null;
  url: string | null;
  title: string;
  abstract: string | null;
  contentText: string | null;
  createdAt: Date;
  createdBy: string | null;
};

export type PaperInput = {
  title: string;
  doi?: string | null;
  pubmedId?: string | null;
  url?: string | null;
  abstract?: string | null;
  contentText?: string | null;
};

/**
 * Upsert a paper by its strongest identifier — DOI preferred, then PubMed
 * id, otherwise fall through to a plain insert. The partial unique indexes
 * on (doi) and (pubmed_id) enforce uniqueness only when present.
 */
export async function upsertPaper(input: PaperInput, createdBy: string): Promise<{ id: string }> {
  if (input.title.length === 0 || input.title.length > 1000) {
    throw new Error('paper title must be 1-1000 chars');
  }
  const values = {
    doi: input.doi ?? null,
    pubmedId: input.pubmedId ?? null,
    url: input.url ?? null,
    title: input.title,
    abstract: input.abstract ?? null,
    contentText: input.contentText ?? null,
    createdBy,
  };
  const set = {
    url: values.url,
    title: values.title,
    abstract: values.abstract,
    contentText: values.contentText,
  };
  if (input.doi) {
    const [row] = await db
      .insert(papers)
      .values(values)
      .onConflictDoUpdate({ target: papers.doi, set })
      .returning({ id: papers.id });
    if (!row) throw new Error('upsertPaper: insert returned no row (doi branch)');
    return row;
  }
  if (input.pubmedId) {
    const [row] = await db
      .insert(papers)
      .values(values)
      .onConflictDoUpdate({ target: papers.pubmedId, set })
      .returning({ id: papers.id });
    if (!row) throw new Error('upsertPaper: insert returned no row (pubmedId branch)');
    return row;
  }
  const [row] = await db.insert(papers).values(values).returning({ id: papers.id });
  if (!row) throw new Error('upsertPaper: insert returned no row');
  return row;
}

export async function getPaperByDoi(doi: string): Promise<PaperRow | null> {
  const [row] = await db.select().from(papers).where(eq(papers.doi, doi));
  return row ?? null;
}

/**
 * FTS over title + abstract. Returns the most-recent matches first; for a
 * known DOI / PubMed id, use the keyed accessors instead.
 */
export async function searchPapersByFTS(query: string, limit = 10): Promise<PaperRow[]> {
  const safeLimit = Math.min(Math.max(1, limit), 50);
  return db
    .select()
    .from(papers)
    .where(sql`to_tsvector('english', coalesce(${papers.title}, '') || ' ' || coalesce(${papers.abstract}, ''))
              @@ plainto_tsquery('english', ${query})`)
    .orderBy(desc(papers.createdAt))
    .limit(safeLimit);
}
