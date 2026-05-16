import { findSimilarReactions } from '@chemclaw2/db';
import { similaritySearchTool } from './similarity-search';

export const reactionSimilaritySearchTool = similaritySearchTool({
  name: 'find_similar_reactions',
  description:
    'Find reactions in the registry similar to a query reaction using DRFP fingerprint similarity. ' +
    'Call compute_drfp (mcp-rxnfp) first to obtain the fingerprint_bits string, then pass it here.',
  fingerprintBitsDescription:
    'DRFP fingerprint as a 2048-char binary string (0/1), from compute_drfp.fingerprint_bits',
  subagents: ['deep-research'],
  search: (bits, limit, min) => findSimilarReactions(bits, limit, min),
});
