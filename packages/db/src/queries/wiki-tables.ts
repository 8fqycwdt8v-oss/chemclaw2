import { eq, asc, sql } from 'drizzle-orm';
import { db } from '../client';
import { wikiTables } from '../schema/wiki-tables';

/**
 * Wave-2c B2: structured representation of a markdown pipe-table found in a
 * wiki body. Headers and rows are kept as JSONB so the same row can be queried
 * structurally (`rows @> '[{"yield":"75%"}]'::jsonb`) without parsing prose.
 *
 * The extractor is intentionally cheap and tolerant:
 *   - Recognizes the standard `| h1 | h2 |\n|---|---|\n| r1 | r2 |` shape
 *   - Strips outer pipes and trims each cell
 *   - Headers / cells past the header count are dropped from that row
 *   - Header-less or divider-less candidates are ignored
 *
 * Anything more elaborate (alignment markers, escaping inside cells, nested
 * inline marks) is over-engineering for the chemistry-table corpus we expect.
 */
export type ExtractedTable = {
  position: number;
  anchor?: string;        // nearest preceding heading text
  headers: string[];
  rows: Array<Record<string, string>>;
};

const DIVIDER_RE = /^\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$/;

function splitRow(line: string): string[] {
  const stripped = line.trim().replace(/^\|/, '').replace(/\|$/, '');
  return stripped.split('|').map((c) => c.trim());
}

export function extractMarkdownTables(md: string): ExtractedTable[] {
  const lines = md.replace(/\r\n/g, '\n').split('\n');
  const out: ExtractedTable[] = [];
  let position = 0;
  let lastHeading: string | undefined;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    const headingMatch = /^#{1,6}\s+(.+)$/.exec(line.trim());
    if (headingMatch) {
      lastHeading = headingMatch[1].trim();
      continue;
    }
    if (!line.includes('|')) continue;
    const next = lines[i + 1];
    if (!next || !DIVIDER_RE.test(next.trim())) continue;
    const headers = splitRow(line);
    if (headers.length < 2) continue;

    const rows: Array<Record<string, string>> = [];
    let j = i + 2;
    while (j < lines.length) {
      const rowLine = lines[j];
      if (!rowLine.includes('|') || rowLine.trim().length === 0) break;
      const cells = splitRow(rowLine);
      if (cells.length === 0) break;
      const row: Record<string, string> = {};
      headers.forEach((h, idx) => { row[h] = cells[idx] ?? ''; });
      rows.push(row);
      j++;
    }

    if (rows.length > 0) {
      out.push({ position: position++, anchor: lastHeading, headers, rows });
    }
    i = j - 1;
  }
  return out;
}

/**
 * Replace the page's wiki_tables rows with the new extraction in one
 * transaction. Called inside `upsertWikiPage`'s transaction so table state
 * mirrors page state.
 *
 * `embedHeader` is optional; when provided, the header_text is embedded so
 * the semantic-search path can surface tables. Passing undefined skips the
 * embedding call (cheap fallback).
 */
export async function upsertTablesForPage(
  pageId: string,
  tables: ExtractedTable[],
  embedHeader?: (text: string) => Promise<number[]>,
): Promise<void> {
  await db.transaction(async (tx) => {
    await tx.delete(wikiTables).where(eq(wikiTables.pageId, pageId));
    if (tables.length === 0) return;
    // Compute embeddings outside the transaction would be cleaner, but the
    // caller already runs inside one; embed-then-insert here keeps the
    // table data atomic with the page write. For high-table pages a future
    // refactor can pre-compute outside.
    const rows = [];
    for (const t of tables) {
      const headerText = t.headers.join(' | ');
      const embedding = embedHeader ? await embedHeader(headerText) : null;
      rows.push({
        pageId,
        position: t.position,
        anchor: t.anchor ?? null,
        headers: t.headers,
        rows: t.rows,
        headerText,
        headerEmbedding: embedding,
      });
    }
    await tx.insert(wikiTables).values(rows);
  });
}

export async function listTablesForPage(pageId: string): Promise<Array<{
  id: string;
  position: number;
  anchor: string | null;
  headers: string[];
  rows: Array<Record<string, string>>;
}>> {
  const rows = await db
    .select({
      id: wikiTables.id,
      position: wikiTables.position,
      anchor: wikiTables.anchor,
      headers: wikiTables.headers,
      rows: wikiTables.rows,
    })
    .from(wikiTables)
    .where(eq(wikiTables.pageId, pageId))
    .orderBy(asc(wikiTables.position));
  return rows.map((r) => ({
    id: r.id,
    position: r.position,
    anchor: r.anchor,
    headers: r.headers as string[],
    rows: r.rows as Array<Record<string, string>>,
  }));
}

/**
 * FTS over wiki_tables.header_text — for "find tables about yields" queries
 * that the future lookup_knowledge orchestrator (or a dedicated tool) may
 * want to surface alongside paragraph chunks.
 */
export async function searchTablesByHeader(query: string, limit = 10): Promise<Array<{
  id: string;
  pageId: string;
  headers: string[];
  rows: Array<Record<string, string>>;
}>> {
  const rows = await db
    .select({
      id: wikiTables.id,
      pageId: wikiTables.pageId,
      headers: wikiTables.headers,
      rows: wikiTables.rows,
    })
    .from(wikiTables)
    .where(sql`to_tsvector('english', ${wikiTables.headerText})
              @@ plainto_tsquery('english', ${query})`)
    .limit(Math.min(Math.max(1, limit), 50));
  return rows.map((r) => ({
    id: r.id,
    pageId: r.pageId,
    headers: r.headers as string[],
    rows: r.rows as Array<Record<string, string>>,
  }));
}
