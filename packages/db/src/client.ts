import { drizzle } from 'drizzle-orm/postgres-js';
import { sql } from 'drizzle-orm';
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
    // max:2 per worker instance — use a connection pooler (PgBouncer/Supavisor) in front of Postgres for higher throughput
    _sql = postgres(connectionString, { max: 2 });
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

export async function withUserContext<T>(userId: string, fn: (tx: typeof db) => Promise<T>): Promise<T> {
  return getDb().transaction(async (tx) => {
    await tx.execute(sql`SET LOCAL app.current_user_id = ${userId}`);
    return fn(tx as unknown as typeof db);
  });
}
