# Migrations

Forward-only SQL migrations applied by `tsx src/migrate.ts` (alias:
`pnpm --filter @chemclaw2/db migrate`). The runner reads files in lexical
order from this directory and tracks applied filenames in a metadata table.

## Ordering

The drizzle-orm migrator reads `meta/_journal.json` — **not the directory**.
Every new SQL file MUST have a matching entry in `_journal.json` with a
monotonically increasing `idx` and a `tag` equal to the file's basename
(no `.sql`). Files without journal entries are silently skipped, and CI
will not catch the omission.

Prefer `drizzle-kit generate` to author migrations: it writes the SQL,
the journal entry, and the schema snapshot in one shot. Hand-authored
SQL files (the project's historical pattern) need a hand-edited journal
entry — keep the `when` close to the commit date so ordering matches
intent if anyone later re-runs against a partially-migrated DB.

Two existing files share prefix `0029_` (`tool_perm_check_and_eval_runs`
and `wiki_tables_cleanup`); both should have journal entries, but only
the second currently does — see BACKLOG.

## Applying

```
DATABASE_URL=postgres://… pnpm --filter @chemclaw2/db migrate
```

CI runs against a fresh `pgvector/pgvector:pg16` instance so no dirty-row
remediation is required. Production / long-running staging clusters may
have rows that predate constraints — pre-flight before applying any
migration that adds `CHECK` or `UNIQUE`:

```
SELECT DISTINCT status FROM synthesis_campaigns;
SELECT DISTINCT status FROM campaign_steps;
SELECT DISTINCT maturity FROM wiki_pages;
SELECT DISTINCT period FROM project_budgets;
```

Outliers should be cleaned up via a one-shot data migration **applied
before** the constraint migration. Do not relax the constraint to
accommodate dirty data.

## Rollback

There is no down-migration tooling. Rollback strategy: restore the
database from a pre-migration snapshot, or write a forward migration that
explicitly reverses the change. Postgres `ALTER TABLE … DROP CONSTRAINT`
and `DROP INDEX` are cheap; reversing data migrations is the harder case.
