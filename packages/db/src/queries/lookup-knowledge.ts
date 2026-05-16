import { sql, eq } from 'drizzle-orm';
import { db } from '../client';
import { wikiPages, wikiChunks } from '../schema/wiki';
import { properties } from '../schema/properties';
import { papers } from '../schema/papers';
import { externalFacts } from '../schema/external-facts';

/**
 * Wave-2b C3: unified knowledge retrieval.
 *
 * The agent today has to choose between five distinct retrieval tools
 * (`wiki_lookup` FTS, `wiki_lookup` semantic, `compound_similarity_search`,
 * `find_similar_reactions`, `eln_fetch`, `lookup_properties`, …) and merge
 * results in prose. `lookup_knowledge` collapses the text-driven retrieval
 * surface into one tool and fuses results across:
 *
 *   - wiki_pages (FTS over content_text)
 *   - wiki_chunks (semantic, optional — requires an embedFn caller)
 *   - papers (FTS over title + abstract)
 *   - properties (exact + ILIKE on name; helps "yield"-style queries surface
 *     measured values for any compound)
 *   - external_facts (FTS over content_text — past tool-fetch results that
 *     v2.2 Wave 2a now persists as world-state)
 *
 * Compound and reaction similarity stay on dedicated tools — they take
 * fingerprints, not free text, and the cost profile is different (HNSW ANN +
 * re-rank, no FTS).
 *
 * Ranks are fused with Reciprocal Rank Fusion (k=60): a tried-and-true,
 * parameter-light blend that doesn't require score normalization across
 * heterogeneous lists.
 */

export type KnowledgeHitType = 'wiki' | 'paper' | 'property' | 'external';

export type KnowledgeHit = {
  type: KnowledgeHitType;
  id: string;
  title: string;
  excerpt: string;
  metadata: Record<string, unknown>;
  /** Best per-list rank (1-indexed) the item achieved before fusion. */
  bestRank: number;
};

export type LookupKnowledgeOptions = {
  /** Max hits returned across all types. */
  limit?: number;
  /** Per-source over-fetch. Default 10; tune up for higher-recall queries. */
  perSourceLimit?: number;
  /** Subset of types to consult. Default: all four. */
  types?: KnowledgeHitType[];
  /** Provide to enable semantic retrieval over wiki chunks. Omit for FTS-only. */
  embedFn?: (text: string) => Promise<number[]>;
};

const RRF_K = 60;

/**
 * Reciprocal Rank Fusion. Each ranked list contributes 1 / (k + rank) to each
 * item's score. The de-duplication key is `${type}:${id}` so the same entity
 * appearing in two lists (e.g. wiki page returned by FTS AND semantic) is
 * counted twice — that's the point: higher consensus → higher rank.
 *
 * `bestRank` is preserved (smaller wins) so the caller can break ties or
 * surface a "found in N of M lists" diagnostic if needed.
 */
export function rrfFuse(lists: KnowledgeHit[][], k: number = RRF_K): KnowledgeHit[] {
  const scores = new Map<string, number>();
  const items = new Map<string, KnowledgeHit>();
  for (const list of lists) {
    list.forEach((hit, rank) => {
      const key = `${hit.type}:${hit.id}`;
      scores.set(key, (scores.get(key) ?? 0) + 1 / (k + rank));
      const prev = items.get(key);
      if (!prev || hit.bestRank < prev.bestRank) {
        items.set(key, { ...hit, bestRank: Math.min(hit.bestRank, prev?.bestRank ?? Infinity) });
      }
    });
  }
  return [...scores.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([key]) => items.get(key)!)
    .filter((v): v is KnowledgeHit => v !== undefined);
}

async function lookupWikiFTS(query: string, limit: number): Promise<KnowledgeHit[]> {
  const rows = await db
    .select({
      id: wikiPages.id,
      slug: wikiPages.slug,
      title: wikiPages.title,
      contentText: wikiPages.contentText,
      maturity: wikiPages.maturity,
    })
    .from(wikiPages)
    .where(sql`to_tsvector('english', coalesce(${wikiPages.contentText}, ''))
              @@ plainto_tsquery('english', ${query})
              AND ${wikiPages.archived} = false`)
    .limit(limit);
  return rows.map((r, i) => ({
    type: 'wiki' as const,
    id: r.id,
    title: r.title,
    excerpt: (r.contentText ?? '').slice(0, 300),
    metadata: { slug: r.slug, maturity: r.maturity, source: 'fts' },
    bestRank: i + 1,
  }));
}

async function lookupWikiSemantic(
  query: string,
  embedFn: (t: string) => Promise<number[]>,
  limit: number,
): Promise<KnowledgeHit[]> {
  const embedding = await embedFn(query);
  if (embedding.length !== 1536) return [];
  const vecStr = `[${embedding.join(',')}]`;
  const distExpr = sql<number>`wiki_chunks.embedding <=> ${sql.param(vecStr)}::vector(1536)`;
  const rows = await db
    .select({
      pageId: wikiChunks.pageId,
      slug: wikiPages.slug,
      title: wikiPages.title,
      text: wikiChunks.text,
      maturity: wikiPages.maturity,
      distance: distExpr,
    })
    .from(wikiChunks)
    .innerJoin(wikiPages, eq(wikiPages.id, wikiChunks.pageId))
    .where(sql`wiki_chunks.embedding IS NOT NULL AND ${wikiPages.archived} = false`)
    .orderBy(distExpr)
    .limit(limit * 3); // over-fetch for per-page dedup
  const seen = new Set<string>();
  const hits: KnowledgeHit[] = [];
  for (const r of rows) {
    if (seen.has(r.pageId)) continue;
    seen.add(r.pageId);
    hits.push({
      type: 'wiki',
      id: r.pageId,
      title: r.title,
      excerpt: r.text.length > 300 ? r.text.slice(0, 300) + '…' : r.text,
      metadata: { slug: r.slug, maturity: r.maturity, source: 'semantic', distance: r.distance },
      bestRank: hits.length + 1,
    });
    if (hits.length >= limit) break;
  }
  return hits;
}

async function lookupPapers(query: string, limit: number): Promise<KnowledgeHit[]> {
  const rows = await db
    .select({
      id: papers.id,
      title: papers.title,
      abstract: papers.abstract,
      doi: papers.doi,
    })
    .from(papers)
    .where(sql`to_tsvector('english',
                coalesce(${papers.title}, '') || ' ' || coalesce(${papers.abstract}, ''))
              @@ plainto_tsquery('english', ${query})`)
    .limit(limit);
  return rows.map((r, i) => ({
    type: 'paper' as const,
    id: r.id,
    title: r.title,
    excerpt: (r.abstract ?? '').slice(0, 300),
    metadata: { doi: r.doi },
    bestRank: i + 1,
  }));
}

async function lookupProperties(query: string, limit: number): Promise<KnowledgeHit[]> {
  // Properties don't have a long-form text body. The retrievable surfaces are
  // name (e.g. "yield", "logP") and value_text. Use ILIKE for partial matches
  // on both; the table is bounded by SAR data volume.
  const rows = await db
    .select({
      id: properties.id,
      compoundId: properties.compoundId,
      name: properties.name,
      valueNum: properties.valueNum,
      valueText: properties.valueText,
      unit: properties.unit,
    })
    .from(properties)
    .where(sql`${properties.name} ILIKE ${'%' + query + '%'}
              OR ${properties.valueText} ILIKE ${'%' + query + '%'}`)
    .limit(limit);
  return rows.map((r, i) => ({
    type: 'property' as const,
    id: r.id,
    title: `${r.name}${r.unit ? ` (${r.unit})` : ''}`,
    excerpt: r.valueText ?? (r.valueNum != null ? String(r.valueNum) : ''),
    metadata: { compound_id: r.compoundId, name: r.name, value_num: r.valueNum, unit: r.unit },
    bestRank: i + 1,
  }));
}

async function lookupExternal(query: string, limit: number): Promise<KnowledgeHit[]> {
  const rows = await db
    .select({
      id: externalFacts.id,
      sourceType: externalFacts.sourceType,
      sourceId: externalFacts.sourceId,
      contentText: externalFacts.contentText,
      lastSeen: externalFacts.lastSeen,
    })
    .from(externalFacts)
    .where(sql`to_tsvector('english', coalesce(${externalFacts.contentText}, ''))
              @@ plainto_tsquery('english', ${query})`)
    .limit(limit);
  return rows.map((r, i) => ({
    type: 'external' as const,
    id: r.id,
    title: `${r.sourceType}: ${r.sourceId.slice(0, 80)}`,
    excerpt: (r.contentText ?? '').slice(0, 300),
    metadata: { source_type: r.sourceType, source_id: r.sourceId, last_seen: r.lastSeen },
    bestRank: i + 1,
  }));
}

export async function lookupKnowledge(
  query: string,
  opts: LookupKnowledgeOptions = {},
): Promise<KnowledgeHit[]> {
  const limit = Math.min(Math.max(1, opts.limit ?? 10), 50);
  const perSource = Math.min(Math.max(1, opts.perSourceLimit ?? 10), 50);
  const types: KnowledgeHitType[] = opts.types ?? ['wiki', 'paper', 'property', 'external'];

  const lists = await Promise.all([
    types.includes('wiki') ? lookupWikiFTS(query, perSource) : null,
    types.includes('wiki') && opts.embedFn
      ? lookupWikiSemantic(query, opts.embedFn, perSource)
      : null,
    types.includes('paper') ? lookupPapers(query, perSource) : null,
    types.includes('property') ? lookupProperties(query, perSource) : null,
    types.includes('external') ? lookupExternal(query, perSource) : null,
  ]);
  const nonNull = lists.filter((l): l is KnowledgeHit[] => l !== null);
  return rrfFuse(nonNull).slice(0, limit);
}
