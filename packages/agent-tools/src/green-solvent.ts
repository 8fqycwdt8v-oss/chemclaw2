import { z } from 'zod';
import data from './data/solvent-greenness.json';
import type { ToolDef } from './tool-def';

type SolventRow = {
  name: string;
  smiles: string;
  chem21: number;
  gsk: number;
  pfizer: number;
  sanofi: number;
  acs: number;
};

const GUIDES = ['chem21', 'gsk', 'pfizer', 'sanofi', 'acs'] as const;
type Guide = (typeof GUIDES)[number];

// Index by canonical SMILES (lowercase, whitespace-trimmed) for O(1) lookup.
const BY_SMILES = new Map<string, SolventRow>();
for (const row of data.solvents as SolventRow[]) {
  BY_SMILES.set(row.smiles.toLowerCase().trim(), row);
}

function meanScore(r: SolventRow): number {
  return GUIDES.reduce((acc, g) => acc + r[g], 0) / GUIDES.length;
}

/**
 * Score one or more solvent SMILES against the curated green-chemistry guides
 * (CHEM21, GSK, Pfizer, Sanofi, ACS). For each match returns per-guide scores
 * 0-10 (higher = greener) and a mean. For unmatched SMILES returns null +
 * the closest-greener suggestions ranked by mean score.
 *
 * The agent should call this with the canonical SMILES of each solvent in a
 * reaction (typically obtained via mcp-molfp.validate_smiles).
 */
const schema = {
  solvents: z.array(z.string()).describe(
    'Solvent SMILES, ideally canonicalised first via mcp-molfp.validate_smiles',
  ),
};

export const greenSolventTool: ToolDef<typeof schema> = {
  name: 'score_solvents',
  description:
    'Score solvent choices against green-chemistry guides (CHEM21, GSK, Pfizer, ' +
    'Sanofi, ACS). Input is an array of solvent SMILES; output is per-guide ' +
    'scores 0-10 plus safer-solvent suggestions when a solvent is red-flagged.',
  schema,
  async execute(input) {
    if (!Array.isArray(input.solvents) || input.solvents.length === 0) {
      return { error: 'solvents must be a non-empty array of SMILES strings' };
    }
    if (input.solvents.length > 20) {
      return { error: 'at most 20 solvents per call' };
    }

    const all = data.solvents as SolventRow[];
    const sortedByMean = [...all].sort((a, b) => meanScore(b) - meanScore(a));

    const results = input.solvents.map((s) => {
      const key = s.toLowerCase().trim();
      const row = BY_SMILES.get(key);
      if (!row) {
        return {
          smiles: s,
          matched: false,
          note: 'Solvent not in the curated guide set. Suggest canonicalising via mcp-molfp.validate_smiles first; otherwise compare manually.',
        };
      }
      const scores = Object.fromEntries(GUIDES.map((g) => [g, row[g]])) as Record<Guide, number>;
      const mean = meanScore(row);
      const flagged = mean < 4;
      const suggestions = flagged
        ? sortedByMean.slice(0, 5).map((r) => ({ name: r.name, smiles: r.smiles, mean_score: meanScore(r) }))
        : [];
      return {
        smiles: s,
        matched: true,
        name: row.name,
        scores,
        mean_score: mean,
        flagged_unsafe: flagged,
        suggestions,
      };
    });

    return { guides: GUIDES, results };
  },
};
