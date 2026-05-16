import { pgTable, uuid, text, timestamp, unique } from 'drizzle-orm/pg-core';

export const toolPermissions = pgTable('tool_permissions', {
  id: uuid('id').primaryKey().defaultRandom(),
  scope: text('scope').notNull(),
  scopeId: text('scope_id').notNull(),
  toolName: text('tool_name').notNull(),
  mode: text('mode').notNull(),
  updatedBy: text('updated_by').notNull(),
  updatedAt: timestamp('updated_at', { withTimezone: true }).notNull().defaultNow(),
}, (t) => [unique('tool_permissions_unique').on(t.scope, t.scopeId, t.toolName)]);
