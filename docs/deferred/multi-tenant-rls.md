# Multi-tenant RLS

## Status

Deferred. Trigger: tenants > 1.

## Context

RLS was disabled across 24 tables (migrations 0034 + 0043) because the
`USING (true)` stubs were a footgun, not enforcement. App-layer authz
via Entra + owner-scoped queries is the actual enforcement today.

## Re-enable checklist

1. Add `org_id uuid REFERENCES orgs(id) NOT NULL` (or equivalent) to
   every per-tenant table.
2. Backfill `org_id` from the existing `created_by → users.org_id` join.
3. Enable RLS with real predicates:

   ```sql
   ALTER TABLE wiki_pages ENABLE ROW LEVEL SECURITY;
   CREATE POLICY wiki_pages_tenant ON wiki_pages
       USING (org_id = current_setting('app.org_id')::uuid);
   ```

4. Wire `SET LOCAL app.org_id = '<uuid>'` into every transaction.
   The `withUserContext` helper from migration 0021 was exported but
   never invoked — adapt that or write a SQLAlchemy session-begin event
   listener that runs the `SET LOCAL` before any query.
5. Integration test: a query with a wrong org id MUST return zero rows
   even for an admin.

## Risks

- `SET LOCAL` is per-transaction. SQLAlchemy's autocommit/autobegin
  semantics make it easy to lose. Test before shipping.
- Some queries cross-tenant (admin dashboards, audit). Mark them with
  `SET app.org_id` (no LOCAL) inside an admin-only route — and make sure
  the admin check fails closed.

## Value

Hard isolation at the DB layer means a query bug stops at the row
boundary instead of leaking data. The cost is real (≈ 1 week of work +
schema migration + thorough testing) and only pays off once there's a
second tenant.
