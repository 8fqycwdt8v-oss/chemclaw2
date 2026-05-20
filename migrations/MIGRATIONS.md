# Migration policy

Plain SQL files in `migrations/`, applied in alphanumeric order by
`.github/workflows/ci.yml` (and the same loop at deploy). No
Alembic / no auto-generation — Postgres-specific features (advisory
locks, pgvector, `BIT`-typed fingerprints, partial indexes, etc.) are
hand-written.

## Numbering

- Sequential `NNNN_short_description.sql`, zero-padded to four digits.
- **Before adding a new migration**, run
  `git fetch origin main && ls migrations/` and pick a number strictly
  higher than every file on `main`. Stale-base branches silently collide
  on the slot otherwise.
- If two migrations land at the same number across branches and both
  reach `main`, rename the loser to `NNNNa_*.sql` (then `NNNNb`, …).
  Historical precedent: `0029a_wiki_tables_cleanup.sql`. The
  alphanumeric apply order keeps the suffix lexicographically after the
  unsuffixed file, which matches the intended semantic order.

## Apply behaviour

Each `.sql` is applied via:

```
psql --single-transaction -v ON_ERROR_STOP=1 -f <file>
```

Wrapped in `set -e` so the loop bails on the first failure. **Implications:**

- A migration must be a single, self-consistent transaction. If you need
  multiple `BEGIN; … COMMIT;` blocks, split into separate files.
- A failing migration rolls back cleanly; the next CI / deploy retries
  the *same* file. Make non-trivial DDL idempotent (`IF NOT EXISTS`,
  `IF EXISTS`, `ALTER … IF EXISTS`) so the retry doesn't trip on the
  partial state from a previous successful step.

## Indexes

**Use `CREATE INDEX CONCURRENTLY` for new index migrations on tables that
already have writes in production.** A plain `CREATE INDEX` takes an
`ACCESS EXCLUSIVE` lock on the table for the duration of the build,
blocking every write. `CONCURRENTLY` does the work without that lock.

```sql
-- Recommended for any new index on a non-empty production table:
CREATE INDEX CONCURRENTLY IF NOT EXISTS my_table_col_idx
    ON my_table (col);
```

**Constraints**: `CONCURRENTLY` cannot run inside a transaction. If a
migration file mixes index creation with other DDL, split the index out
into its own file. The CI apply step detects files containing the word
`CONCURRENTLY` (case-insensitive grep) and applies them with autocommit
instead of `--single-transaction`. Such files **must be
single-statement** — `ON_ERROR_STOP` is per-statement under autocommit,
so a partial mid-file failure would land partial state otherwise.
Reference: `migrations/0041_paper_chunks_hnsw.sql`.

**Existing pre-policy indexes** (0001–0036, plus 0036_performance_indexes
specifically) were created without `CONCURRENTLY`. They are already
applied in production; rewriting the historical migrations would have no
effect. If a future maintenance window requires rebuilding any of them,
do so with `DROP INDEX … CONCURRENTLY` + `CREATE INDEX … CONCURRENTLY`
in a fresh migration.

## Schema mutations

- `ALTER TABLE … ADD COLUMN … DEFAULT <const>` — Postgres ≥ 11 stores
  the default in the catalog without rewriting the table; safe.
- `ALTER TABLE … ADD COLUMN … DEFAULT <volatile>` (e.g. `now()`,
  `gen_random_uuid()`) — rewrites the whole table under an
  `ACCESS EXCLUSIVE` lock. Split into:
  1. `ADD COLUMN … DEFAULT NULL` (cheap).
  2. Backfill in batches in app code or a follow-up migration.
  3. `ALTER COLUMN … SET DEFAULT <volatile>` once backfilled.
  4. `ALTER COLUMN … SET NOT NULL` when every row has a value.
- `ALTER TABLE … DROP COLUMN` — fast in Postgres (it marks the column
  hidden, doesn't rewrite). Reclaim space via `VACUUM FULL` only if
  needed.
- `ALTER TABLE … ADD CONSTRAINT … FOREIGN KEY … NOT VALID` then
  `VALIDATE CONSTRAINT` later — avoids the long lock that
  `ADD CONSTRAINT … FOREIGN KEY` (no `NOT VALID`) takes.

## RLS

CLAUDE.md §security-2: don't enable RLS without per-tenant predicates.
`USING (true)` is footgun, not policy. Either land real predicates in
the same migration or leave RLS off until you can.

## Verifying locally

```
docker run --rm -e POSTGRES_PASSWORD=postgres -p 5432:5432 -d \
    --name chemclaw2-pg pgvector/pgvector:pg16
psql postgres://postgres:postgres@localhost:5432/postgres \
    -c "CREATE DATABASE chemclaw2_test;"
for f in migrations/*.sql; do
    psql postgres://postgres:postgres@localhost:5432/chemclaw2_test \
        --single-transaction -v ON_ERROR_STOP=1 -f "$f" || exit 1
done
```

If you've added a migration that depends on a previous one, also test
the *idempotent re-apply* path by running the loop twice — the second
pass must be a no-op (or fail cleanly with `IF NOT EXISTS` / `IF EXISTS`
guards in place).
