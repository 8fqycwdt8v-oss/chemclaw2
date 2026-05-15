import { findSimilarReactions } from '@chemclaw2/db';

export const reactionSimilaritySearchTool = {
  name: 'find_similar_reactions',
  description:
    'Find reactions in the registry similar to a query reaction using DRFP fingerprint similarity. ' +
    'Call compute_drfp (mcp-rxnfp) first to obtain the fingerprint_bits string, then pass it here.',
  inputSchema: {
    type: 'object' as const,
    properties: {
      fingerprint_bits: {
        type: 'string',
        description: 'DRFP fingerprint as a 2048-char binary string (0/1), from compute_drfp.fingerprint_bits',
      },
      min_similarity: { type: 'number', description: 'Minimum similarity score (0–1)', default: 0.4 },
      limit: { type: 'number', description: 'Max results to return', default: 20 },
    },
    required: ['fingerprint_bits'],
  },
  async execute(input: { fingerprint_bits: string; min_similarity?: number; limit?: number }) {
    const results = await findSimilarReactions(
      input.fingerprint_bits,
      input.limit ?? 20,
      input.min_similarity ?? 0.4,
    );
    return { results };
  },
};
