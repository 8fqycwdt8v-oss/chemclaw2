import { drizzle } from 'drizzle-orm/postgres-js';
import postgres from 'postgres';
import { trace } from '@opentelemetry/api';
import * as schema from './schema/index';

// Lazy initialization: defer validation until first DB call so Next.js
// can bundle and statically analyze the route without DATABASE_URL present.
let _sql: ReturnType<typeof postgres> | undefined;
let _db: ReturnType<typeof drizzle<typeof schema>> | undefined;

// Pool sizing: assumes a PgBouncer/Supavisor pooler in front of Postgres. In
// transaction-pooling mode the per-process `max` is fine to raise; in session
// mode the upstream pool grows. Tune via DB_POOL_MAX env (default 5).
function poolMax(): number {
  const raw = process.env.DB_POOL_MAX;
  if (!raw) return 5;
  const n = Number.parseInt(raw, 10);
  return Number.isFinite(n) && n > 0 && n <= 50 ? n : 5;
}

function getDb() {
  if (!_db) {
    const connectionString = process.env.DATABASE_URL;
    if (!connectionString) throw new Error('DATABASE_URL is required');
    _sql = postgres(connectionString, {
      max: poolMax(),
      onnotice: () => {},
      // Surface socket / pool errors so OTel + Langfuse see them instead of
      // silently dropping queries on transient network issues.
      onclose: (connId) => {
        trace.getActiveSpan()?.addEvent('db_connection_closed', { conn_id: String(connId) });
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
