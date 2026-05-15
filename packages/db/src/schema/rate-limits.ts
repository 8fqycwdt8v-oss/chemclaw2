import { pgTable, text, bigint, integer, index } from 'drizzle-orm/pg-core';

export const rateLimits = pgTable(
  'rate_limits',
  {
    key: text('key').notNull(),
    windowStart: bigint('window_start', { mode: 'number' }).notNull(),
    count: integer('count').notNull().default(1),
  },
  (t) => [index('rate_limits_window_idx').on(t.windowStart)],
);
