import { findSimilarReactions } from '@chemclaw2/db';

export const reactionSimilaritySearchTool = {
  name: 'find_similar_reactions',
  description:
    'Find reactions in the registry similar to a query reaction SMILES using DRFP fingerprint similarity.',
  inputSchema: {
    type: 'object' as const,
    properties: {
      reaction_smiles: {
        type: 'string',
        description: 'Query reaction SMILES (reactants>>products format)',
      },
      min_similarity: { type: 'number', description: 'Minimum similarity score (0-1)', default: 0.4 },
      limit: { type: 'number', description: 'Max results', default: 20 },
    },
    required: ['reaction_smiles'],
  },
  async execute(input: { reaction_smiles: string; min_similarity?: number; limit?: number }) {
    const results = await findSimilarReactions(
      input.reaction_smiles,
      input.limit ?? 20,
      input.min_similarity ?? 0.4,
    );
    return { results };
  },
};
