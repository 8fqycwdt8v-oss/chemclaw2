import { pgTable, text, bigint, integer, timestamp, primaryKey } from 'drizzle-orm/pg-core';

export const projectBudgets = pgTable('project_budgets', {
  projectKey: text('project_key').primaryKey(),
  period: text('period').notNull(),
  toolCallsCap: bigint('tool_calls_cap', { mode: 'number' }),
  experimentsCap: integer('experiments_cap'),
  // Wave-2c: LLM input+output token cap per period.
  tokensCap: bigint('tokens_cap', { mode: 'number' }),
  updatedBy: text('updated_by').notNull(),
  updatedAt: timestamp('updated_at', { withTimezone: true }).notNull().defaultNow(),
  createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
});

export const projectBudgetSpend = pgTable('project_budget_spend', {
  projectKey: text('project_key').notNull(),
  periodStart: timestamp('period_start', { withTimezone: true }).notNull(),
  toolCalls: bigint('tool_calls', { mode: 'number' }).notNull().default(0),
  experiments: integer('experiments').notNull().default(0),
  // Wave-2c: accumulated input+output tokens for the period. Cache tokens
  // are excluded — they're effectively free and would punish cache-friendly
  // prompts.
  tokens: bigint('tokens', { mode: 'number' }).notNull().default(0),
  updatedAt: timestamp('updated_at', { withTimezone: true }).notNull().defaultNow(),
}, (t) => [primaryKey({ columns: [t.projectKey, t.periodStart] })]);
