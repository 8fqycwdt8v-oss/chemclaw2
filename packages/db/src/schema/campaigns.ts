import { pgTable, uuid, text, jsonb, timestamp, integer, boolean } from 'drizzle-orm/pg-core';
import { wikiPages } from './wiki';

export const synthesisCampaigns = pgTable('synthesis_campaigns', {
  id: uuid('id').primaryKey().defaultRandom(),
  sessionId: text('session_id').notNull(),
  targetSmiles: text('target_smiles'),
  status: text('status').notNull().default('planning'),
  plan: jsonb('plan'),
  wikiPageId: uuid('wiki_page_id').references(() => wikiPages.id),
  createdBy: text('created_by').notNull(),
  createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
  updatedAt: timestamp('updated_at', { withTimezone: true }).notNull().defaultNow(),
});

export const campaignSteps = pgTable('campaign_steps', {
  id: uuid('id').primaryKey().defaultRandom(),
  campaignId: uuid('campaign_id').notNull().references(() => synthesisCampaigns.id, { onDelete: 'cascade' }),
  stepIdx: integer('step_idx').notNull(),
  reactionSmiles: text('reaction_smiles'),
  conditions: text('conditions'),
  status: text('status').notNull().default('pending'),
  result: jsonb('result'),
  retryCount: integer('retry_count').notNull().default(0),
  nextRetryAt: timestamp('next_retry_at', { withTimezone: true }),
  requiresApproval: boolean('requires_approval').notNull().default(false),
  updatedAt: timestamp('updated_at', { withTimezone: true }).notNull().defaultNow(),
});
