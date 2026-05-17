import { drizzle } from 'drizzle-orm/postgres-js';
import postgres from 'postgres';
import * as schema from './schema/index';

// Lazy initialization: defer validation until first DB call so Next.js
// can bundle and statically analyze the route without DATABASE_URL present.
let _sql: ReturnType<typeof postgres> | undefined;
let _db: ReturnType<typeof drizzle<typeof schema>> | undefined;

function getDb() {
  if (!_db) {
    const connectionString = process.env.DATABASE_URL;
    if (!connectionString) throw new Error('DATABASE_URL is required');
    // Wave-3h perf: lifted from max:2 → max:15 (configurable via DB_POOL_MAX)
    // to match the v2.2 fan-out shape. lookup_knowledge alone issues up to
    // 5 parallel queries; per-tool-call budget hooks add another DB write.
    // With max:2 a single high-fan-out tool call saturated the pool and
    // serialized concurrent users. 15 is a defensible floor for a pgbouncer
    // transaction-mode pooler; raise via env if a bigger upstream demands.
    const max = Number(process.env.DB_POOL_MAX ?? '15');
    _sql = postgres(connectionString, { max });
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
