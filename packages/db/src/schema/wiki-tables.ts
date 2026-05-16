import { customType, pgTable, uuid, text, integer, timestamp, jsonb, index } from 'drizzle-orm/pg-core';
import { wikiPages } from './wiki';

// pgvector 1536-dim cosine vector — matches wiki_chunks embedding shape.
const vector1536 = customType<{ data: number[] | null; driverData: string | null }>({
  dataType: () => 'vector(1536)',
  toDriver: (v) => (v == null ? null : `[${v.join(',')}]`),
});

export const wikiTables = pgTable('wiki_tables', {
  id: uuid('id').primaryKey().defaultRandom(),
  pageId: uuid('page_id').notNull().references(() => wikiPages.id, { onDelete: 'cascade' }),
  position: integer('position').notNull(),
  anchor: text('anchor'),
  headers: jsonb('headers').notNull(),
  rows: jsonb('rows').notNull(),
  headerText: text('header_text').notNull(),
  headerEmbedding: vector1536('header_embedding'),
  createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
}, (t) => [index('wiki_tables_page_idx').on(t.pageId, t.position)]);
