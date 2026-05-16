import { eq, and, desc, sql } from 'drizzle-orm';
import { db } from '../client';
import { properties } from '../schema/properties';

export type PropertyInput = {
  compoundId: string;
  name: string;
  valueNum?: number | null;
  valueText?: string | null;
  unit?: string | null;
  method?: string | null;
  sourceCitationId?: string | null;
  measuredAt?: Date | null;
};

export type PropertyRow = {
  id: string;
  compoundId: string;
  name: string;
  valueNum: number | null;
  valueText: string | null;
  unit: string | null;
  method: string | null;
  sourceCitationId: string | null;
  measuredAt: Date | null;
  createdAt: Date;
  createdBy: string;
};

export async function insertProperty(
  input: PropertyInput,
  createdBy: string,
): Promise<{ id: string }> {
  if (input.valueNum == null && (input.valueText == null || input.valueText.length === 0)) {
    throw new Error('property requires valueNum or non-empty valueText');
  }
  if (input.name.length === 0 || input.name.length > 200) {
    throw new Error('property name must be 1-200 chars');
  }
  const [row] = await db
    .insert(properties)
    .values({
      compoundId: input.compoundId,
      name: input.name,
      valueNum: input.valueNum ?? null,
      valueText: input.valueText ?? null,
      unit: input.unit ?? null,
      method: input.method ?? null,
      sourceCitationId: input.sourceCitationId ?? null,
      measuredAt: input.measuredAt ?? null,
      createdBy,
    })
    .returning({ id: properties.id });
  return row;
}

/**
 * Bulk insert in one round-trip. Used when an extraction pass produces
 * many property rows for the same compound.
 */
export async function insertProperties(
  inputs: PropertyInput[],
  createdBy: string,
): Promise<number> {
  if (inputs.length === 0) return 0;
  for (const i of inputs) {
    if (i.valueNum == null && (i.valueText == null || i.valueText.length === 0)) {
      throw new Error('every property requires valueNum or non-empty valueText');
    }
  }
  const rows = await db
    .insert(properties)
    .values(inputs.map((i) => ({
      compoundId: i.compoundId,
      name: i.name,
      valueNum: i.valueNum ?? null,
      valueText: i.valueText ?? null,
      unit: i.unit ?? null,
      method: i.method ?? null,
      sourceCitationId: i.sourceCitationId ?? null,
      measuredAt: i.measuredAt ?? null,
      createdBy,
    })))
    .returning({ id: properties.id });
  return rows.length;
}

export type PropertyFilters = {
  name?: string;
  /** Numeric range filter; either bound is optional. Both bounds are inclusive. */
  valueNumGte?: number;
  valueNumLte?: number;
  unit?: string;
};

/**
 * List properties for a compound. With `filters.name = 'yield'` and a range
 * predicate the caller can ask "what yields did we measure for compound X
 * in the 60-100 % range" — the table makes that a single SQL query instead
 * of parsing markdown prose.
 */
export async function listPropertiesForCompound(
  compoundId: string,
  filters: PropertyFilters = {},
  limit = 50,
): Promise<PropertyRow[]> {
  const preds = [eq(properties.compoundId, compoundId)];
  if (filters.name) preds.push(eq(properties.name, filters.name));
  if (filters.unit) preds.push(eq(properties.unit, filters.unit));
  if (filters.valueNumGte != null) {
    preds.push(sql`${properties.valueNum} >= ${filters.valueNumGte}`);
  }
  if (filters.valueNumLte != null) {
    preds.push(sql`${properties.valueNum} <= ${filters.valueNumLte}`);
  }
  return db
    .select()
    .from(properties)
    .where(and(...preds))
    .orderBy(desc(properties.measuredAt), desc(properties.createdAt))
    .limit(Math.min(Math.max(1, limit), 500));
}
