import { pgTable, uuid, timestamp, integer, jsonb, text } from 'drizzle-orm/pg-core';

export type EvalProbeResult = {
  name: string;
  passed: boolean;
  durationMs: number;
  error?: string;
};

export const evalRuns = pgTable('eval_runs', {
  id: uuid('id').primaryKey().defaultRandom(),
  startedAt: timestamp('started_at', { withTimezone: true }).notNull().defaultNow(),
  finishedAt: timestamp('finished_at', { withTimezone: true }),
  fixturesTotal: integer('fixtures_total').notNull(),
  fixturesPassed: integer('fixtures_passed').notNull(),
  scores: jsonb('scores').$type<EvalProbeResult[]>().notNull().default([]),
  notes: text('notes'),
});
