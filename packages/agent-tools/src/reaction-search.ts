import { z } from 'zod';
import { findSimilarReactions } from '@chemclaw2/db';
import type { ToolDef } from './tool-def';

const schema = {
  fingerprint_bits: z.string().describe(
    'DRFP fingerprint as a 2048-char binary string (0/1), from compute_drfp.fingerprint_bits',
  ),
  min_similarity: z.number().min(0).max(1).optional().describe('Minimum similarity score (0–1)'),
  limit: z.number().int().min(1).max(50).optional().describe('Max results to return'),
};

export const reactionSimilaritySearchTool: ToolDef<typeof schema> = {
  name: 'find_similar_reactions',
  description:
    'Find reactions in the registry similar to a query reaction using DRFP fingerprint similarity. ' +
    'Call compute_drfp (mcp-rxnfp) first to obtain the fingerprint_bits string, then pass it here.',
  schema,
  async execute(input) {
    try {
      const results = await findSimilarReactions(
        input.fingerprint_bits,
        input.limit ?? 20,
        input.min_similarity ?? 0.4,
      );
      return { results };
    } catch (err) {
      return { error: err instanceof Error ? err.message : 'Search failed' };
    }
  },
};
