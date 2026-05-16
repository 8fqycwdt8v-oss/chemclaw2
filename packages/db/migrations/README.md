# Migrations

Forward-only SQL migrations applied by `tsx src/migrate.ts` (alias:
`pnpm --filter @chemclaw2/db migrate`). The runner reads files in lexical
order from this directory and tracks applied filenames in a metadata table.

## Ordering

Files are numbered `NNNN_<slug>.sql`. **Lexical order is the contract** — do
not renumber existing files, only append. Two existing migrations share
prefix `0029_` (`0029_tool_perm_check_and_eval_runs.sql` and
`0029_wiki_tables_cleanup.sql`); they sort deterministically by the second
half of the name.

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
