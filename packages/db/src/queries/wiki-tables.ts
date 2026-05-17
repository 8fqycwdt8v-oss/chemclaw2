import { eq, asc } from 'drizzle-orm';
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

export const TABLE_DIVIDER_RE = /^\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$/;
const FENCE_RE = /^\s*```/;

function splitRow(line: string): string[] {
  const stripped = line.trim().replace(/^\|/, '').replace(/\|$/, '');
  return stripped.split('|').map((c) => c.trim());
}

export function extractMarkdownTables(md: string): ExtractedTable[] {
  const lines = md.replace(/\r\n/g, '\n').split('\n');
  const out: ExtractedTable[] = [];
  let position = 0;
  let lastHeading: string | undefined;
  // Wave-3h: skip content inside fenced code blocks so pipe-tables in
  // documentation about markdown don't get parsed as real tables.
  let inFence = false;

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (FENCE_RE.test(line)) {
      inFence = !inFence;
      continue;
    }
    if (inFence) continue;
    const headingMatch = /^#{1,6}\s+(.+)$/.exec(line.trim());
    if (headingMatch) {
      lastHeading = headingMatch[1].trim();
      continue;
    }
    if (!line.includes('|')) continue;
    const next = lines[i + 1];
    if (!next || !TABLE_DIVIDER_RE.test(next.trim())) continue;
    const headers = splitRow(line);
    if (headers.length < 2) continue;

    const rows: Array<Record<string, string>> = [];
    let j = i + 2;
    while (j < lines.length) {
      const rowLine = lines[j];
      if (!rowLine.includes('|') || rowLine.trim().length === 0) break;
      // Wave-3h: handle adjacent tables. The current line is either:
      //   - a divider — terminator (e.g. table-after-divider, no data in
      //     between)
      //   - looks-like-a-header AND the NEXT line is a divider — this is
      //     actually the next table's header row; stop here so we don't
      //     absorb it as data of the current table.
      if (TABLE_DIVIDER_RE.test(rowLine.trim())) break;
      const peek = lines[j + 1];
      if (peek && TABLE_DIVIDER_RE.test(peek.trim())) break;
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
 * Wave-3f: header-embedding parameter dropped along with the column
 * (migration 0029). When semantic table retrieval is genuinely needed, the
 * right shape is bundling header strings into the wiki_upsert's single
 * embedFn call alongside chunks — not an extra per-table OpenAI round-trip.
 */
export async function upsertTablesForPage(
  pageId: string,
  tables: ExtractedTable[],
): Promise<void> {
  await db.transaction(async (tx) => {
    await tx.delete(wikiTables).where(eq(wikiTables.pageId, pageId));
    if (tables.length === 0) return;
    await tx.insert(wikiTables).values(tables.map((t) => ({
      pageId,
      position: t.position,
      anchor: t.anchor ?? null,
      headers: t.headers,
      rows: t.rows,
    })));
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

// searchTablesByHeader removed in Wave-3f: no caller, no tool surface. When
// "find tables about yields" becomes a real need, the right home for it is
// inside lookup_knowledge's RRF fan-out, not a separate query helper.
