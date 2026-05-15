import { pgTable, uuid, text, jsonb, timestamp } from 'drizzle-orm/pg-core';

export const auditLog = pgTable('audit_log', {
  id: uuid('id').primaryKey().defaultRandom(),
  tableName: text('table_name').notNull(),
  rowId: uuid('row_id').notNull(),
  operation: text('operation').notNull(),
  oldData: jsonb('old_data'),
  newData: jsonb('new_data'),
  changedBy: text('changed_by'),
  changedAt: timestamp('changed_at', { withTimezone: true }).notNull().defaultNow(),
});
