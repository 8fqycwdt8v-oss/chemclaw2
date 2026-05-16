import { z } from 'zod';
import { insertProperties } from '@chemclaw2/db';
import { UUID_RE } from './uuid';
import type { ToolDef } from './tool-def';

const propertyItemSchema = z.object({
  compound_id: z.string().describe('UUID of the compound'),
  name: z.string().describe('Property name (e.g. "yield", "logP")'),
  value_num: z.number().optional(),
  value_text: z.string().optional(),
  unit: z.string().optional(),
  method: z.string().optional(),
  source_citation_id: z.string().optional().describe('wiki_citations.citation_id this came from'),
  measured_at: z.string().optional().describe('ISO-8601 timestamp'),
});
const schema = {
  properties: z.array(propertyItemSchema).describe('Up to 100 property rows to insert in one batch'),
};

/**
 * Wave-3e B6 write tool: bulk-insert structured property rows (SAR data) for
 * known compounds. The entity-extractor sub-agent calls this after parsing a
 * wiki body for measurements like "yield 75%, catalyst Pd/C" or
 * "logP = 2.1 (measured by Crippen)".
 *
 * Batch interface: extraction passes often produce many rows for the same
 * page, and one round-trip keeps the sub-agent's tool-call count down.
 * `source_citation_id` ties each row back to the wiki_citations entry the
 * value was sourced from, so the trail back to the source paper / experiment
 * stays intact.
 */
export function createRegisterPropertyTool(userId: string): ToolDef<typeof schema> {
  return {
    name: 'register_compound_property',
    description:
      'Persist structured measurement rows for known compounds. Use after ' +
      'extracting numeric or categorical SAR data from a wiki body. Each ' +
      'row must have either a numeric value (value_num) or a free-text ' +
      'value (value_text). Returns the count inserted; on validation error ' +
      'returns { error } and inserts NOTHING (no partial writes).',
    schema,
    async execute(input) {
      if (!Array.isArray(input.properties) || input.properties.length === 0) {
        return { error: 'properties must be a non-empty array' };
      }
      if (input.properties.length > 100) {
        return { error: 'properties must be ≤100 per call (split into batches)' };
      }
      const inputs = [];
      for (const p of input.properties) {
        if (!UUID_RE.test(p.compound_id)) {
          return { error: `invalid compound_id: ${p.compound_id}` };
        }
        if (p.name.length === 0 || p.name.length > 200) {
          return { error: `property name must be 1-200 chars (got "${p.name.slice(0, 40)}")` };
        }
        const hasNum = typeof p.value_num === 'number' && Number.isFinite(p.value_num);
        const hasText = typeof p.value_text === 'string' && p.value_text.length > 0;
        if (!hasNum && !hasText) {
          return { error: `property "${p.name}" must have value_num or non-empty value_text` };
        }
        let measuredAt: Date | undefined;
        if (p.measured_at) {
          measuredAt = new Date(p.measured_at);
          if (isNaN(measuredAt.getTime())) {
            return { error: `invalid measured_at for "${p.name}": must be ISO-8601` };
          }
        }
        inputs.push({
          compoundId: p.compound_id,
          name: p.name,
          valueNum: hasNum ? p.value_num : null,
          valueText: hasText ? p.value_text : null,
          unit: p.unit ?? null,
          method: p.method ?? null,
          sourceCitationId: p.source_citation_id ?? null,
          measuredAt,
        });
      }
      try {
        const inserted = await insertProperties(inputs, userId);
        return { inserted };
      } catch (err) {
        return { error: err instanceof Error ? err.message : 'insertProperties failed' };
      }
    },
  };
}
