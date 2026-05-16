import { callMcpTool } from './mcp-client';
import data from './data/nmr-shifts.json';

type ShiftRow = { smarts: string; name: string; shift_range: [number, number] };
type Nucleus = '1H' | '13C';

/**
 * Heuristic NMR-shift prediction: for each functional-group SMARTS in our
 * curated table, ask mcp-molfp.substructure_match against the target SMILES.
 * If it matches, add the corresponding chemical-shift range to the output.
 *
 * This is a coarse predictor meant to anchor the LLM's spectrum interpretation.
 * It is NOT a DFT or HOSE-code predictor — those would need a dedicated server
 * (deferred per the v2 plan). Multiple ranges may overlap; the agent should
 * report each.
 */
export const nmrPredictTool = {
  name: 'predict_nmr',
  description:
    'Predict approximate NMR chemical-shift ranges for the functional groups ' +
    'present in a SMILES, for 1H or 13C. Returns one entry per matched group ' +
    'with name + δ range. Output is heuristic — use to anchor peak assignments, ' +
    'not as ground truth.',
  inputSchema: {
    type: 'object' as const,
    properties: {
      smiles: { type: 'string' },
      nucleus: { type: 'string', enum: ['1H', '13C'], description: 'Default 1H' },
    },
    required: ['smiles'],
  },
  async execute(input: { smiles: string; nucleus?: Nucleus }) {
    const nucleus = input.nucleus ?? '1H';
    const smiles = input.smiles.trim();
    if (smiles.length === 0 || smiles.length > 1000) {
      return { error: 'smiles is required (≤1000 chars)' };
    }
    const table = (data as unknown as Record<string, ShiftRow[]>)[nucleus];
    if (!Array.isArray(table)) return { error: `unsupported nucleus: ${nucleus}` };

    const matches: Array<{ group: string; shift_range: [number, number] }> = [];
    for (const row of table) {
      try {
        const res = await callMcpTool('mcp_molfp.server', 'substructure_match', {
          smiles,
          smarts: row.smarts,
        }, { timeoutMs: 5_000 });
        if (res.match === true) {
          matches.push({ group: row.name, shift_range: row.shift_range });
        }
      } catch {
        // Skip individual SMARTS failures; the table is curated but RDKit may
        // reject exotic patterns. The result is still useful with partial data.
      }
    }
    return {
      nucleus,
      smiles,
      predictions: matches,
      note: 'Coarse functional-group ranges. Use as an anchor, not ground truth.',
    };
  },
};
