import { pgTable, uuid, text, jsonb, timestamp, unique, index } from 'drizzle-orm/pg-core';

export const externalFacts = pgTable('external_facts', {
  id: uuid('id').primaryKey().defaultRandom(),
  sourceType: text('source_type').notNull(),
  sourceId: text('source_id').notNull(),
  payload: jsonb('payload').notNull(),
  contentText: text('content_text'),
  firstSeen: timestamp('first_seen', { withTimezone: true }).notNull().defaultNow(),
  lastSeen: timestamp('last_seen', { withTimezone: true }).notNull().defaultNow(),
  fetchedBy: text('fetched_by').notNull(),
}, (t) => [
  unique('external_facts_source_unique').on(t.sourceType, t.sourceId),
  index('external_facts_lookup_idx').on(t.sourceType, t.sourceId),
]);
