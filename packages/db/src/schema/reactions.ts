import { customType, pgTable, text, timestamp, uuid } from 'drizzle-orm/pg-core';

const bit2048 = customType<{ data: string }>({
  dataType: () => 'bit(2048)',
});

export const reactions = pgTable('reactions', {
  id: uuid('id').primaryKey().defaultRandom(),
  rxnSmiles: text('rxn_smiles').notNull(),
  name: text('name'),
  conditions: text('conditions'),
  drfp: bit2048('drfp'),
  fpComputedAt: timestamp('fp_computed_at', { withTimezone: true }),
  createdAt: timestamp('created_at', { withTimezone: true }).defaultNow().notNull(),
  createdBy: text('created_by').notNull(),
});
