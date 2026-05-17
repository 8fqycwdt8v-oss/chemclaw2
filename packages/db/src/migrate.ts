import { drizzle } from 'drizzle-orm/postgres-js';
import { migrate } from 'drizzle-orm/postgres-js/migrator';
import postgres from 'postgres';
import { fileURLToPath } from 'url';
import { dirname, join } from 'path';
import { dbEnv } from './env';

const __dirname = dirname(fileURLToPath(import.meta.url));

async function runMigrations() {
  const { DATABASE_URL } = dbEnv();
  const sql = postgres(DATABASE_URL, { max: 1 });
  const db = drizzle(sql);

  console.log('Running migrations...');
  await migrate(db, { migrationsFolder: join(__dirname, '../migrations') });
  console.log('Migrations complete.');

  await sql.end();
}

runMigrations().catch((err) => {
  console.error(err);
  process.exit(1);
});
