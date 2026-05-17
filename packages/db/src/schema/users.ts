import { pgTable, text, timestamp, index } from 'drizzle-orm/pg-core';

/**
 * Clerk-synced user mirror. Populated by the /api/webhooks/clerk route on
 * user.created / user.updated / user.deleted events. Clerk remains the source
 * of truth for auth and role; this table exists so we can:
 *   - join user identity into audit trails without an extra Clerk API call,
 *   - query "who has admin role in this org" without iterating publicMetadata,
 *   - retain a soft record after a user is deleted in Clerk (deletedAt).
 *
 * `userId` is the Clerk user ID (e.g. user_2abc…) — used everywhere else in
 * the schema as `text user_id`, so this row's PK matches those FKs by value.
 */
export const users = pgTable('users', {
  userId: text('user_id').primaryKey(),
  email: text('email'),
  role: text('role'),
  createdAt: timestamp('created_at', { withTimezone: true }).notNull().defaultNow(),
  updatedAt: timestamp('updated_at', { withTimezone: true }).notNull().defaultNow(),
  deletedAt: timestamp('deleted_at', { withTimezone: true }),
}, (t) => [
  index('users_role_idx').on(t.role),
]);
