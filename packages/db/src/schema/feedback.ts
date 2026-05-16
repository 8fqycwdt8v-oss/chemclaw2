import { pgTable, uuid, text, integer, timestamp, unique } from 'drizzle-orm/pg-core';

export const agentFeedback = pgTable('agent_feedback', {
  id: uuid('id').primaryKey().defaultRandom(),
  sessionId: text('session_id').notNull(),
  turnIndex: integer('turn_index').notNull(),
  score: integer('score').notNull(),
  reason: text('reason'),
  userId: text('user_id').notNull(),
  createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
}, (t) => [unique('agent_feedback_session_turn_user').on(t.sessionId, t.turnIndex, t.userId)]);

export const agentOverrides = pgTable('agent_overrides', {
  id: uuid('id').primaryKey().defaultRandom(),
  sessionId: text('session_id').notNull(),
  userId: text('user_id').notNull(),
  gateName: text('gate_name').notNull(),
  justification: text('justification').notNull(),
  promptHash: text('prompt_hash').notNull(),
  createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
});
