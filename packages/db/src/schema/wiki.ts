import { pgTable, uuid, text, jsonb, timestamp, integer, boolean, unique } from 'drizzle-orm/pg-core';
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
  needsReview: boolean('needs_review').notNull().default(false),
  archived: boolean('archived').notNull().default(false),
  maturity: text('maturity').notNull().default('exploratory'),
  project: text('project'),
  validFrom: timestamp('valid_from', { withTimezone: true }).notNull().defaultNow(),
  validTo: timestamp('valid_to', { withTimezone: true }),
});

export const wikiChunks = pgTable('wiki_chunks', {
  id: uuid('id').primaryKey().defaultRandom(),
  pageId: uuid('page_id').notNull().references(() => wikiPages.id, { onDelete: 'cascade' }),
  chunkIdx: integer('chunk_idx').notNull(),
  text: text('text').notNull(),
  embedding: vector1536('embedding'),
}, (t) => [unique('wiki_chunks_page_chunk_unique').on(t.pageId, t.chunkIdx)]);

export const wikiCitations = pgTable('wiki_citations', {
  id: uuid('id').primaryKey().defaultRandom(),
  pageId: uuid('page_id').notNull().references(() => wikiPages.id, { onDelete: 'cascade' }),
  citationId: text('citation_id').notNull(),
  sourceType: text('source_type').notNull(),
  sourceId: text('source_id'),
  label: text('label').notNull(),
  disputed: boolean('disputed').notNull().default(false),
});

export const wikiSubscriptions = pgTable('wiki_subscriptions', {
  userId: text('user_id').notNull(),
  pageId: uuid('page_id').notNull().references(() => wikiPages.id, { onDelete: 'cascade' }),
  lastSeenVersion: integer('last_seen_version').notNull().default(0),
  createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
}, (t) => [unique('wiki_subscriptions_pk').on(t.userId, t.pageId)]);
