import { z } from 'zod';
import { findSimilarCompounds } from '@chemclaw2/db';
import type { ToolDef } from './tool-def';
import { toolError } from './tool-error';

const schema = {
  fingerprint_bits: z.string().describe(
    'Morgan fingerprint as a 2048-char binary string (0/1), from compute_morgan_fp.fingerprint_bits',
  ),
  min_tanimoto: z.number().min(0).max(1).optional().describe('Minimum Tanimoto score (0–1)'),
  limit: z.number().int().min(1).max(50).optional().describe('Max results to return'),
};

export const compoundSimilaritySearchTool: ToolDef<typeof schema> = {
  name: 'compound_similarity_search',
  description:
    'Find compounds in the registry similar to a query molecule using Morgan/ECFP4 fingerprint Tanimoto similarity. ' +
    'Call compute_morgan_fp (mcp-molfp) first to obtain the fingerprint_bits string, then pass it here.',
  schema,
  async execute(input) {
    try {
      const results = await findSimilarCompounds(
        input.fingerprint_bits,
        input.limit ?? 20,
        input.min_tanimoto ?? 0.4,
      );
      return { results };
    } catch (err) {
      return toolError('compound_similarity_search', err);
    }
  },
};
