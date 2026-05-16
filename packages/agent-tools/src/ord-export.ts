import { db, reactions } from '@chemclaw2/db';
import { inArray } from 'drizzle-orm';

type Reaction = typeof reactions.$inferSelect;

/**
 * Serialize one or more reactions into an ORD (Open Reaction Database) JSON
 * shape. We emit the JSON form of the protobuf message, which is the
 * interchange format ORD itself uses. Not all fields are populated — only the
 * ones we have data for. Importers can read this and merge it into their own
 * ORD datasets.
 *
 * Schema reference: https://github.com/open-reaction-database/ord-schema
 */
function reactionToOrd(r: Reaction): Record<string, unknown> {
  // Parse "reactants>reagents>products" or "reactants>>products"
  const parts = r.rxnSmiles.split('>');
  const reactants = parts[0] ?? '';
  const products = parts[parts.length - 1] ?? '';

  return {
    reaction_id: r.id,
    identifiers: [{ type: 'REACTION_SMILES', value: r.rxnSmiles }],
    inputs: reactants.split('.').filter(Boolean).map((s, idx) => ({
      [`reactant_${idx}`]: {
        components: [{
          identifiers: [{ type: 'SMILES', value: s }],
          amount: { volume: null, mass: null, moles: null },
          reaction_role: 'REACTANT',
        }],
      },
    })),
    outcomes: products.split('.').filter(Boolean).map((s) => ({
      products: [{
        identifiers: [{ type: 'SMILES', value: s }],
        reaction_role: 'PRODUCT',
      }],
    })),
    conditions: r.conditions ? { conditions_are_dynamic: false, details: r.conditions } : undefined,
    notes: r.name ? { procedure_details: r.name } : undefined,
    provenance: {
      record_created: { time: { value: r.createdAt.toISOString() }, person: { username: r.createdBy } },
      record_modified: [{ time: { value: r.createdAt.toISOString() }, person: { username: r.createdBy } }],
    },
  };
}

/**
 * Bulk export. Caller supplies up to 100 reaction IDs; returns one ORD JSON
 * object per reaction (or null for missing IDs). Wrapped by the /api routes.
 */
export async function exportReactionsAsOrd(ids: string[]): Promise<Array<Record<string, unknown> | null>> {
  if (ids.length === 0) return [];
  if (ids.length > 100) throw new Error('at most 100 reactions per export');
  const rows = await db.select().from(reactions).where(inArray(reactions.id, ids));
  const byId = new Map(rows.map((r) => [r.id, r]));
  return ids.map((id) => {
    const r = byId.get(id);
    return r ? reactionToOrd(r) : null;
  });
}
