import { pgTable, text, timestamp, uuid, integer } from 'drizzle-orm/pg-core';
import { bit2048 } from './custom-types';

export const compounds = pgTable('compounds', {
  id: uuid('id').primaryKey().defaultRandom(),
  smiles: text('smiles').notNull(),
  canonSmiles: text('canon_smiles'),
  name: text('name'),
  casNumber: text('cas_number'),
  morganFp: bit2048('morgan_fp'),
  // Wave-2a opportunity #4: bit_count(morgan_fp) cached at write time so the
  // Tanimoto re-rank can skip one popcount per row. Generated STORED column,
  // null when morgan_fp is null.
  morganFpPopcount: integer('morgan_fp_popcount'),
  fpComputedAt: timestamp('fp_computed_at', { withTimezone: true }),
  createdAt: timestamp('created_at', { withTimezone: true }).defaultNow().notNull(),
  createdBy: text('created_by').notNull(),
});
