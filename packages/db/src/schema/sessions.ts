import { pgTable, text, jsonb, bigint, bigserial, index, primaryKey } from 'drizzle-orm/pg-core';

export const agentSessions = pgTable(
  'agent_sessions',
  {
    projectKey: text('project_key').notNull(),
    sessionId:  text('session_id').notNull(),
    subpath:    text('subpath').notNull().default(''),
    entries:    jsonb('entries').notNull().default([]),
    mtime:      bigint('mtime', { mode: 'number' }).notNull(),
    insertSeq:  bigserial('insert_seq', { mode: 'number' }),
  },
  (t) => [
    primaryKey({ columns: [t.projectKey, t.sessionId, t.subpath] }),
    index('agent_sessions_mtime_idx').on(t.projectKey, t.mtime),
    index('agent_sessions_insert_seq_idx').on(t.projectKey, t.sessionId, t.insertSeq),
  ]
);
