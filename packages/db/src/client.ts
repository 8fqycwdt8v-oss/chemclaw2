import { drizzle } from 'drizzle-orm/postgres-js';
import postgres from 'postgres';
import { trace } from '@opentelemetry/api';
import * as schema from './schema/index';
import { dbEnv } from './env';

// Lazy initialization: defer validation until first DB call so Next.js
// can bundle and statically analyze the route without DATABASE_URL present.
// DB_POOL_MAX: pgbouncer transaction-mode pooler; lookup_knowledge alone
// issues up to 5 parallel queries, plus per-tool-call budget writes — 15 is
// a defensible floor. Raise via env if a bigger upstream demands.
let _sql: ReturnType<typeof postgres> | undefined;
let _db: ReturnType<typeof drizzle<typeof schema>> | undefined;

function getDb() {
  if (!_db) {
    const { DATABASE_URL, DB_POOL_MAX } = dbEnv();
    _sql = postgres(DATABASE_URL, {
      max: DB_POOL_MAX,
      onnotice: () => {},
      // Surface socket / pool errors so OTel + Langfuse see them instead of
      // silently dropping queries on transient network issues.
      onclose: (connId) => {
        trace.getActiveSpan()?.addEvent('db.connection_closed', { conn_id: String(connId) });
      },
    });
    _db = drizzle(_sql, { schema });
  }
  return _db;
}

// Proxy so callers use `db.query`, `db.select` etc. without change.
export const db = new Proxy({} as ReturnType<typeof drizzle<typeof schema>>, {
  get(_target, prop) {
    return getDb()[prop as keyof ReturnType<typeof drizzle<typeof schema>>];
  },
});

export function getPgClient() {
  getDb(); // ensure initialized
  return _sql!;
}

// Named export alias for legacy callers that import pgClient directly.
export { getPgClient as pgClient };
