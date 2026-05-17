import { pgTable, text, timestamp, uuid } from 'drizzle-orm/pg-core';
import { bit2048 } from './custom-types';

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
