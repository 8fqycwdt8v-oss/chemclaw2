import { pgTable, uuid, text, timestamp, doublePrecision, index } from 'drizzle-orm/pg-core';
import { compounds } from './compounds';

export const properties = pgTable('properties', {
  id: uuid('id').primaryKey().defaultRandom(),
  compoundId: uuid('compound_id').notNull().references(() => compounds.id, { onDelete: 'cascade' }),
  name: text('name').notNull(),
  valueNum: doublePrecision('value_num'),
  valueText: text('value_text'),
  unit: text('unit'),
  method: text('method'),
  sourceCitationId: text('source_citation_id'),
  measuredAt: timestamp('measured_at', { withTimezone: true }),
  createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
  createdBy: text('created_by').notNull(),
}, (t) => [
  index('properties_compound_name_idx').on(t.compoundId, t.name),
  index('properties_name_idx').on(t.name),
]);
