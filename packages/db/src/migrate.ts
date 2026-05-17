import { drizzle } from 'drizzle-orm/postgres-js';
import { migrate } from 'drizzle-orm/postgres-js/migrator';
import postgres from 'postgres';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import { logger } from '@chemclaw2/observability';

const __dirname = dirname(fileURLToPath(import.meta.url));

async function runMigrations() {
  const connectionString = process.env.DATABASE_URL;
  if (!connectionString) throw new Error('DATABASE_URL is required');

  const sql = postgres(connectionString, { max: 1 });
  const db = drizzle(sql);

  const migrationsFolder = join(__dirname, '../migrations');
  logger.info('migrations_start', { folder: migrationsFolder });
  await migrate(db, { migrationsFolder });
  logger.info('migrations_complete', {});

  await sql.end();
}

runMigrations().catch((err) => {
  logger.error('migrations_failed', {}, err);
  process.exit(1);
});
