import { customType, pgTable, text, timestamp, uuid } from 'drizzle-orm/pg-core';

// pgvector 0.7+ supports HNSW on bit(N) — no 2000-dim cap for bit type
const bit2048 = customType<{ data: string }>({
  dataType: () => 'bit(2048)',
});

export const compounds = pgTable('compounds', {
  id: uuid('id').primaryKey().defaultRandom(),
  smiles: text('smiles').notNull(),
  canonSmiles: text('canon_smiles'),
  name: text('name'),
  casNumber: text('cas_number'),
  morganFp: bit2048('morgan_fp'),
  fpComputedAt: timestamp('fp_computed_at', { withTimezone: true }),
  createdAt: timestamp('created_at', { withTimezone: true }).defaultNow().notNull(),
  createdBy: text('created_by').notNull(),
});
