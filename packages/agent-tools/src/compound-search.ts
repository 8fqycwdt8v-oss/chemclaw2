import { findSimilarCompounds } from '@chemclaw2/db';

export const compoundSimilaritySearchTool = {
  name: 'compound_similarity_search',
  description:
    'Find compounds in the registry similar to a query molecule using Morgan/ECFP4 fingerprint Tanimoto similarity. ' +
    'Call compute_morgan_fp (mcp-molfp) first to obtain the fingerprint_bits string, then pass it here.',
  inputSchema: {
    type: 'object' as const,
    properties: {
      fingerprint_bits: {
        type: 'string',
        description: 'Morgan fingerprint as a 2048-char binary string (0/1), from compute_morgan_fp.fingerprint_bits',
      },
      min_tanimoto: { type: 'number', description: 'Minimum Tanimoto score (0–1)', default: 0.4 },
      limit: { type: 'number', description: 'Max results to return', default: 20 },
    },
    required: ['fingerprint_bits'],
  },
  async execute(input: { fingerprint_bits: string; min_tanimoto?: number; limit?: number }) {
    const results = await findSimilarCompounds(
      input.fingerprint_bits,
      input.limit ?? 20,
      input.min_tanimoto ?? 0.4,
    );
    return { results };
  },
};
