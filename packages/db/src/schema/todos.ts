import { pgTable, uuid, text, integer, timestamp, index } from 'drizzle-orm/pg-core';

export const agentTodos = pgTable('agent_todos', {
  id: uuid('id').primaryKey().defaultRandom(),
  sessionId: text('session_id').notNull(),
  userId: text('user_id').notNull(),
  text: text('text').notNull(),
  status: text('status').notNull().default('pending'),
  position: integer('position').notNull(),
  createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
  updatedAt: timestamp('updated_at', { withTimezone: true }).notNull().defaultNow(),
}, (t) => [index('agent_todos_session_idx').on(t.sessionId, t.position)]);
