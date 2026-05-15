import { pgTable, uuid, text, jsonb, timestamp, integer } from 'drizzle-orm/pg-core';
import { customType } from 'drizzle-orm/pg-core';

// vector(1536) custom type for pgvector
const vector1536 = customType<{ data: number[] }>({
  dataType: () => 'vector(1536)',
  fromDriver: (v: unknown) => {
    if (typeof v === 'string') {
      return v.slice(1, -1).split(',').map(Number);
    }
    throw new Error(`Unexpected vector driver value type: ${typeof v}`);
  },
  toDriver: (v: number[]) => `[${v.join(',')}]`,
});

export const wikiPages = pgTable('wiki_pages', {
  id: uuid('id').primaryKey().defaultRandom(),
  slug: text('slug').notNull().unique(),
  title: text('title').notNull(),
  content: jsonb('content').notNull().default({}),
  contentText: text('content_text'),
  createdBy: text('created_by').notNull(),
  updatedBy: text('updated_by'),
  createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
  updatedAt: timestamp('updated_at', { withTimezone: true }).notNull().defaultNow(),
  version: integer('version').notNull().default(1),
});

export const wikiChunks = pgTable('wiki_chunks', {
  id: uuid('id').primaryKey().defaultRandom(),
  pageId: uuid('page_id').notNull().references(() => wikiPages.id, { onDelete: 'cascade' }),
  chunkIdx: integer('chunk_idx').notNull(),
  text: text('text').notNull(),
  embedding: vector1536('embedding'),
});

export const wikiCitations = pgTable('wiki_citations', {
  id: uuid('id').primaryKey().defaultRandom(),
  pageId: uuid('page_id').notNull().references(() => wikiPages.id, { onDelete: 'cascade' }),
  citationId: text('citation_id').notNull(),
  sourceType: text('source_type').notNull(),
  sourceId: text('source_id'),
  label: text('label').notNull(),
});
