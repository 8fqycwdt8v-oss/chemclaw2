import { pgTable, uuid, text, timestamp } from 'drizzle-orm/pg-core';

export const papers = pgTable('papers', {
  id: uuid('id').primaryKey().defaultRandom(),
  doi: text('doi'),
  pubmedId: text('pubmed_id'),
  url: text('url'),
  title: text('title').notNull(),
  abstract: text('abstract'),
  contentText: text('content_text'),
  createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
  createdBy: text('created_by'),
});
