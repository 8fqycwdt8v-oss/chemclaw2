import { sql } from 'drizzle-orm';
import { db } from '../client';
import { users } from '../schema/users';

/**
 * Upsert a user row from a Clerk webhook payload. INSERT … ON CONFLICT
 * preserves `created_at` on subsequent updates and bumps `updated_at` only on
 * field changes. Clearing `deleted_at` re-activates a soft-deleted row in the
 * (rare) case Clerk emits user.created for a previously-deleted user.
 */
export async function upsertUserFromClerk(input: {
  userId: string;
  email: string | null;
  role: string | null;
}): Promise<void> {
  await db
    .insert(users)
    .values({
      userId: input.userId,
      email: input.email,
      role: input.role,
    })
    .onConflictDoUpdate({
      target: users.userId,
      set: {
        email: input.email,
        role: input.role,
        updatedAt: sql`now()`,
        deletedAt: null,
      },
    });
}

/**
 * Soft-delete: set `deleted_at` so audit joins still resolve the user_id but
 * the row is excluded from "active users" queries. Clerk's user.deleted event
 * fires this; we never hard-delete because foreign keys (wiki_pages.created_by,
 * audit rows, etc.) reference the user_id as TEXT and would orphan on DELETE.
 */
export async function softDeleteUser(userId: string): Promise<void> {
  await db
    .update(users)
    .set({ deletedAt: sql`now()` })
    .where(sql`${users.userId} = ${userId}`);
}
