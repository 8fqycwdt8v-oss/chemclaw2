import { findSimilarCompounds } from '@chemclaw2/db';
import { similaritySearchTool } from './similarity-search';

export const compoundSimilaritySearchTool = similaritySearchTool({
  name: 'compound_similarity_search',
  description:
    'Find compounds in the registry similar to a query molecule using Morgan/ECFP4 fingerprint Tanimoto similarity. ' +
    'Call compute_morgan_fp (mcp-molfp) first to obtain the fingerprint_bits string, then pass it here.',
  fingerprintBitsDescription:
    'Morgan fingerprint as a 2048-char binary string (0/1), from compute_morgan_fp.fingerprint_bits',
  scoreDescription: 'Minimum Tanimoto score (0–1)',
  subagents: ['deep-research', 'entity-extractor'],
  search: (bits, limit, min) => findSimilarCompounds(bits, limit, min),
});
