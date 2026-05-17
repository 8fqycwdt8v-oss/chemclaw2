import { z } from 'zod';
import { listPropertiesForCompound } from '@chemclaw2/db';
import { UUID_RE } from './uuid';
import type { ToolDef } from './tool-def';

const schema = {
  compound_id: z.string().describe('UUID of the compound'),
  name: z.string().optional().describe('Exact property name (e.g. "yield", "logP")'),
  value_num_gte: z.number().optional().describe('Inclusive lower bound on numeric value'),
  value_num_lte: z.number().optional().describe('Inclusive upper bound on numeric value'),
  unit: z.string().optional().describe('Exact unit (e.g. "%", "nM")'),
  limit: z.number().int().min(1).max(500).optional().describe('Max rows (default 50)'),
};

/**
 * Wave-2b B5 agent tool: structured SAR query over the properties table.
 *
 * Use when the question is "show me yields measured for compound X between 60
 * and 100" — i.e. the agent knows the compound id and wants rows back, not
 * prose. For "what do we know about X" use lookup_knowledge instead.
 */
export const lookupPropertiesTool: ToolDef<typeof schema> = {
  name: 'lookup_properties',
  description:
    'Query the structured properties (SAR) table for a single compound. ' +
    'Supports filters on property name, unit, and a numeric value range. ' +
    'Returns rows sorted by measured_at then created_at (most recent first).',
  subagents: ['deep-research'],
  schema,
  async execute(input) {
    if (!UUID_RE.test(input.compound_id)) {
      return { error: 'compound_id must be a UUID' };
    }
    if (input.value_num_gte != null && input.value_num_lte != null
        && input.value_num_gte > input.value_num_lte) {
      return { error: 'value_num_gte must be ≤ value_num_lte' };
    }
    const rows = await listPropertiesForCompound(
      input.compound_id,
      {
        name: input.name,
        unit: input.unit,
        valueNumGte: input.value_num_gte,
        valueNumLte: input.value_num_lte,
      },
      input.limit,
    );
    return {
      compound_id: input.compound_id,
      count: rows.length,
      rows: rows.map((r) => ({
        id: r.id,
        name: r.name,
        value_num: r.valueNum,
        value_text: r.valueText,
        unit: r.unit,
        method: r.method,
        source_citation_id: r.sourceCitationId,
        measured_at: r.measuredAt,
      })),
    };
  },
};
