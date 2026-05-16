import { pgTable, uuid, text, timestamp, jsonb, index } from 'drizzle-orm/pg-core';
import { wikiPages } from './wiki';

export const wikiProposedEdits = pgTable('wiki_proposed_edits', {
  id: uuid('id').primaryKey().defaultRandom(),
  slug: text('slug').notNull(),
  title: text('title').notNull(),
  content: jsonb('content').notNull(),
  contentText: text('content_text').notNull(),
  citations: jsonb('citations').notNull().default('[]'),
  proposedBy: text('proposed_by').notNull(),
  rationale: text('rationale'),
  status: text('status').notNull().default('pending'),
  previousId: uuid('previous_id'),
  reviewedBy: text('reviewed_by'),
  reviewComment: text('review_comment'),
  reviewedAt: timestamp('reviewed_at', { withTimezone: true }),
  appliedPageId: uuid('applied_page_id').references(() => wikiPages.id),
  createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
}, (t) => [
  index('wiki_proposed_edits_slug_idx').on(t.slug, t.createdAt),
  index('wiki_proposed_edits_author_idx').on(t.proposedBy, t.createdAt),
]);
