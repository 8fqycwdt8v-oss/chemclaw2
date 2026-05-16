import { findSimilarCompounds } from '@chemclaw2/db';
import { compoundSimilaritySearchTool } from './compound-search';

/**
 * Interpret analytical observations (NMR / MS / IR) by grounding the LLM in
 * (a) similar compounds from the registry that share the proposed structure,
 * (b) a structured prompt to encourage citations and uncertainty calls.
 *
 * This tool does NOT parse raw spectra files — raw-data analysis is out of
 * v1.5 scope. The user supplies the observed peaks/values as free text; the
 * agent uses similar compounds' known data (from prior wiki pages and the
 * registry) as the interpretation anchor.
 *
 * Returns a payload the agent should incorporate into its next response,
 * not the final interpretation itself.
 */
export const interpretAnalyticalResultTool = {
  name: 'interpret_analytical_result',
  description:
    'Build an interpretation context for analytical observations (NMR / MS / IR). ' +
    'Takes the technique, observations as free text, and optionally a proposed structure SMILES. ' +
    'Returns nearest-neighbor compounds (when a structure is given) so the model can ground its ' +
    'interpretation in prior data. The model should incorporate the context and cite sources.',
  inputSchema: {
    type: 'object' as const,
    properties: {
      technique: { type: 'string', enum: ['NMR', 'MS', 'IR'], description: 'Spectroscopy technique' },
      observations: { type: 'string', description: 'Observed peaks / fragments / signals (free text)' },
      proposed_structure_smiles: {
        type: 'string',
        description: 'Optional SMILES the user thinks matches the data',
      },
      proposed_fingerprint_bits: {
        type: 'string',
        description:
          'Optional Morgan fingerprint (2048-char bit string) for the proposed structure. ' +
          'Pre-compute via mcp-molfp.compute_morgan_fp if a structure is supplied.',
      },
    },
    required: ['technique', 'observations'],
  },
  async execute(input: {
    technique: 'NMR' | 'MS' | 'IR';
    observations: string;
    proposed_structure_smiles?: string;
    proposed_fingerprint_bits?: string;
  }) {
    const obs = input.observations.trim();
    if (!obs) return { error: 'observations is required' };
    if (obs.length > 4000) return { error: 'observations must be ≤ 4000 characters' };

    let neighbors: Awaited<ReturnType<typeof findSimilarCompounds>> = [];
    if (input.proposed_fingerprint_bits) {
      try {
        neighbors = await findSimilarCompounds(input.proposed_fingerprint_bits, 5, 0.3);
      } catch (err) {
        return {
          error: `Neighbor lookup failed: ${err instanceof Error ? err.message : 'unknown'}`,
        };
      }
    }

    return {
      technique: input.technique,
      observations: obs,
      proposed_structure: input.proposed_structure_smiles ?? null,
      nearest_neighbors: neighbors.map((n) => ({
        id: n.id,
        smiles: n.canonSmiles ?? n.smiles,
        name: n.name,
        casNumber: n.casNumber,
        tanimoto: n.tanimoto,
      })),
      guidance: [
        `Interpret the ${input.technique} observations using the nearest-neighbor compounds as a reference (if any).`,
        'Cite specific peaks / signals from the user input.',
        'If the proposed structure does not match the data, state which signals are inconsistent.',
        'Call wiki_lookup for any neighbor name to ground claims in our knowledge base.',
        'When uncertain about a peak assignment, say so explicitly and propose follow-up experiments.',
      ].join(' '),
      // Re-emit the search hint so the agent knows it can refine
      hint: neighbors.length === 0 && input.proposed_structure_smiles
        ? `No nearest neighbors found above Tanimoto 0.3 for the proposed structure. ` +
          `Use ${compoundSimilaritySearchTool.name} with a lower min_tanimoto if needed.`
        : undefined,
    };
  },
};
