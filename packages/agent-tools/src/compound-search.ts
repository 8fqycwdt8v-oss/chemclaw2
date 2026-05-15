import { findSimilarCompounds } from '@chemclaw2/db';

export const compoundSimilaritySearchTool = {
  name: 'compound_similarity_search',
  description:
    'Find compounds in the registry similar to a query SMILES using Morgan/ECFP4 fingerprint Tanimoto similarity.',
  inputSchema: {
    type: 'object' as const,
    properties: {
      smiles: { type: 'string', description: 'Query molecule SMILES' },
      min_tanimoto: { type: 'number', description: 'Minimum Tanimoto score (0-1)', default: 0.4 },
      limit: { type: 'number', description: 'Max results', default: 20 },
    },
    required: ['smiles'],
  },
  async execute(input: { smiles: string; min_tanimoto?: number; limit?: number }) {
    // Morgan fingerprint is computed by calling mcp-molfp via the agent's MCP tool
    // The agent calls mcp__mcp-molfp__compute_morgan_fp first, then passes hex here.
    // This tool wraps the DB query for direct use in agent tool definitions.
    const results = await findSimilarCompounds(
      input.smiles, // treated as hex fp when called from agent after MCP call
      input.limit ?? 20,
      input.min_tanimoto ?? 0.4,
    );
    return { results };
  },
};
