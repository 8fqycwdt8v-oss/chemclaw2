import { listPropertiesForCompound } from '@chemclaw2/db';
import { UUID_RE } from './uuid';

type LookupInput = {
  compound_id: string;
  name?: string;
  value_num_gte?: number;
  value_num_lte?: number;
  unit?: string;
  limit?: number;
};

/**
 * Wave-2b B5 agent tool: structured SAR query over the properties table.
 *
 * Use when the question is "show me yields measured for compound X between 60
 * and 100" — i.e. the agent knows the compound id and wants rows back, not
 * prose. For "what do we know about X" use lookup_knowledge instead.
 */
export const lookupPropertiesTool = {
  name: 'lookup_properties',
  description:
    'Query the structured properties (SAR) table for a single compound. ' +
    'Supports filters on property name, unit, and a numeric value range. ' +
    'Returns rows sorted by measured_at then created_at (most recent first).',
  inputSchema: {
    type: 'object' as const,
    properties: {
      compound_id: { type: 'string', description: 'UUID of the compound' },
      name: { type: 'string', description: 'Exact property name (e.g. "yield", "logP")' },
      value_num_gte: { type: 'number', description: 'Inclusive lower bound on numeric value' },
      value_num_lte: { type: 'number', description: 'Inclusive upper bound on numeric value' },
      unit: { type: 'string', description: 'Exact unit (e.g. "%", "nM")' },
      limit: { type: 'integer', minimum: 1, maximum: 500, description: 'Max rows (default 50)' },
    },
    required: ['compound_id'],
  },
  async execute(input: LookupInput) {
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
