import { pgTable, uuid, text, integer, timestamp, jsonb, index } from 'drizzle-orm/pg-core';
import { wikiPages } from './wiki';

export const wikiTables = pgTable('wiki_tables', {
  id: uuid('id').primaryKey().defaultRandom(),
  pageId: uuid('page_id').notNull().references(() => wikiPages.id, { onDelete: 'cascade' }),
  position: integer('position').notNull(),
  anchor: text('anchor'),
  headers: jsonb('headers').notNull(),
  rows: jsonb('rows').notNull(),
  createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
}, (t) => [index('wiki_tables_page_idx').on(t.pageId, t.position)]);
