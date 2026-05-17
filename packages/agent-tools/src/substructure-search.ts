import { z } from 'zod';
import { listCompoundsForSubstructure } from '@chemclaw2/db';
import type { ToolDef } from './tool-def';
import { toolError } from './tool-error';

const schema = {
  max_candidates: z.number().int().min(1).max(5000).optional().describe(
    'Maximum number of candidates to return (≤5000)',
  ),
};

/**
 * Substructure search agent tool. The agent invokes mcp-molfp.substructure_match
 * for each candidate; this tool returns the candidate list and lets the caller
 * do the per-candidate match (so the LLM sees progress and can stop early).
 *
 * For datasets > ~5k compounds this approach is slow — the RDKit Postgres
 * cartridge is the upgrade path (§5.2). Until then, cap maxCandidates.
 */
export const substructureCandidatesTool: ToolDef<typeof schema> = {
  name: 'list_substructure_candidates',
  description:
    'Return up to maxCandidates compounds whose SMILES should be tested against a SMARTS pattern. ' +
    'Use mcp-molfp substructure_match per candidate to filter. ' +
    'Prefer this only when similarity search is not sufficient — substructure matching is O(N) over the registry.',
  subagents: ['deep-research'],
  schema,
  async execute(input) {
    try {
      const results = await listCompoundsForSubstructure(input.max_candidates ?? 500);
      return { candidates: results };
    } catch (err) {
      return toolError('list_substructure_candidates', err);
    }
  },
};
