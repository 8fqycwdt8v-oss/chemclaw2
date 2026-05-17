import { sql, SQL } from 'drizzle-orm';
import { db } from '../client';
import { compounds } from '../schema/compounds';
import { rerankByTanimoto, validateFpBits } from './fp-utils';

export type SimilarCompound = {
  id: string;
  smiles: string;
  canonSmiles: string | null;
  name: string | null;
  casNumber: string | null;
  tanimoto: number;
};

export type CompoundFilters = {
  /** ISO date string — restrict to compounds created on or after this date. */
  createdAfter?: string;
  /** Require a non-null CAS number. */
  hasCas?: boolean;
};

function buildFilterClause(filters?: CompoundFilters): SQL | undefined {
  if (!filters) return undefined;
  const parts: SQL[] = [];
  if (filters.createdAfter) {
    const dt = new Date(filters.createdAfter);
    if (!isNaN(dt.getTime())) parts.push(sql`created_at >= ${dt.toISOString()}::timestamptz`);
  }
  if (filters.hasCas) parts.push(sql`cas_number IS NOT NULL`);
  if (parts.length === 0) return undefined;
  return parts.reduce((acc, p) => sql`${acc} AND ${p}`);
}

/**
 * Two-stage similarity search:
 *  1. HNSW ANN pre-filter using Hamming distance (fast, ~100 candidates)
 *  2. Exact Tanimoto re-rank in application code (bit_count(a&b) / bit_count(a|b))
 *
 * queryFpBits: binary string of '0'/'1' chars (2048 chars), as returned by
 * the mcp-molfp compute_morgan_fp tool's `fingerprint_bits` field.
 *
 * filters: optional metadata constraints (createdAfter, hasCas). Applied as
 * additional WHERE predicates BEFORE the HNSW ordering so the candidate set
 * is already narrowed when re-ranking.
 */
export async function findSimilarCompounds(
  queryFpBits: string,
  limit = 20,
  minTanimoto = 0.4,
  filters?: CompoundFilters,
): Promise<SimilarCompound[]> {
  validateFpBits(queryFpBits);
  const safeLimit = Math.max(1, Math.min(limit, 100));
  const safeMin = Math.max(0, Math.min(minTanimoto, 1));
  const filterSql = buildFilterClause(filters);
  const where = filterSql ? sql`morgan_fp IS NOT NULL AND ${filterSql}` : sql`morgan_fp IS NOT NULL`;
  const rows = await db
    .select({
      id: compounds.id,
      smiles: compounds.smiles,
      canonSmiles: compounds.canonSmiles,
      name: compounds.name,
      casNumber: compounds.casNumber,
      fp: compounds.morganFp,
    })
    .from(compounds)
    .where(where)
    .orderBy(sql`morgan_fp <~> ${queryFpBits}::bit(2048)`)
    .limit(100);
  return rerankByTanimoto(rows, queryFpBits, safeMin, safeLimit)
    .map(({ fp: _fp, similarity, ...rest }) => ({ ...rest, tanimoto: similarity }));
}

/**
 * Return candidate compounds (id + SMILES) for substructure SMARTS matching.
 * The caller (search API) is expected to invoke mcp-molfp substructure_match
 * per candidate. We deliberately return up to `maxCandidates` rows and leave
 * SMARTS matching off the DB — pgvector + bit_hamming can't do substructure,
 * and the RDKit Postgres cartridge is deferred (see chemclaw2_features.md §5.2).
 *
 * For datasets above ~10k compounds this becomes slow. Trigger to add the
 * cartridge: substructure search latency complaints.
 */
export async function listCompoundsForSubstructure(
  maxCandidates = 1000,
): Promise<Array<{ id: string; smiles: string; canonSmiles: string | null; name: string | null; casNumber: string | null }>> {
  return db
    .select({
      id: compounds.id,
      smiles: compounds.smiles,
      canonSmiles: compounds.canonSmiles,
      name: compounds.name,
      casNumber: compounds.casNumber,
    })
    .from(compounds)
    .limit(Math.min(maxCandidates, 5000));
}

export async function insertCompound(
  smiles: string,
  createdBy: string,
  opts?: { name?: string; casNumber?: string },
): Promise<string> {
  const [row] = await db
    .insert(compounds)
    .values({ smiles, createdBy, name: opts?.name, casNumber: opts?.casNumber })
    .returning({ id: compounds.id });
  if (!row) throw new Error('insertCompound: insert returned no row');
  return row.id;
}
