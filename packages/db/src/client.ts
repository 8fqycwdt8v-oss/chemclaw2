import { drizzle } from 'drizzle-orm/postgres-js';
import postgres from 'postgres';
import * as schema from './schema/index';

const connectionString = process.env.DATABASE_URL;
if (!connectionString) throw new Error('DATABASE_URL is required');

// max:2 per worker instance — use a connection pooler (PgBouncer/Supavisor) in front of Postgres for higher throughput
const sql = postgres(connectionString, { max: 2 });
export const db = drizzle(sql, { schema });
export { sql as pgClient };
