import { z } from 'zod';
import type { SubagentTag, ToolDef } from './tool-def';
import { toolError } from './tool-error';

/** Factory for "search the registry by 2048-bit fingerprint" tools. The
 * compound (Morgan/ECFP4) and reaction (DRFP) variants previously each had
 * a 31-LOC copy of this shape; both now collapse to a 3-line factory call. */
export function similaritySearchTool<R>(opts: {
  name: string;
  description: string;
  fingerprintBitsDescription: string;
  scoreField?: 'min_tanimoto' | 'min_similarity';
  scoreDescription?: string;
  defaultMin?: number;
  subagents?: readonly SubagentTag[];
  search: (fpBits: string, limit: number, minScore: number) => Promise<R>;
}): ToolDef<{
  fingerprint_bits: z.ZodString;
  min_similarity: z.ZodOptional<z.ZodNumber>;
  limit: z.ZodOptional<z.ZodNumber>;
}> {
  const schema = {
    fingerprint_bits: z.string().describe(opts.fingerprintBitsDescription),
    min_similarity: z.number().min(0).max(1).optional().describe(
      opts.scoreDescription ?? 'Minimum similarity score (0–1)',
    ),
    limit: z.number().int().min(1).max(50).optional().describe('Max results to return'),
  };
  return {
    name: opts.name,
    description: opts.description,
    subagents: opts.subagents,
    schema,
    async execute(input) {
      try {
        const results = await opts.search(
          input.fingerprint_bits,
          input.limit ?? 20,
          input.min_similarity ?? (opts.defaultMin ?? 0.4),
        );
        return { results };
      } catch (err) {
        return toolError(opts.name, err);
      }
    },
  };
}
