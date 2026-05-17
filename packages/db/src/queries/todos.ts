import { eq, and, asc, sql } from 'drizzle-orm';
import { db } from '../client';
import { agentTodos } from '../schema/todos';

export type AgentTodo = {
  id: string;
  text: string;
  status: 'pending' | 'done';
  position: number;
  updatedAt: Date;
};

/**
 * Replace the session's todo list with the supplied items in one transaction.
 * Used by kickoff_campaign to seed one entry per step in the campaign plan.
 */
export async function replaceSessionTodos(
  sessionId: string,
  userId: string,
  items: string[],
): Promise<void> {
  await db.transaction(async (tx) => {
    // Defense-in-depth: scope DELETE by userId so a leaked or reused
    // sessionId can't wipe another user's todos. Mirrors setTodoStatus.
    await tx.delete(agentTodos).where(and(
      eq(agentTodos.sessionId, sessionId),
      eq(agentTodos.userId, userId),
    ));
    if (items.length === 0) return;
    await tx.insert(agentTodos).values(
      items.map((text, i) => ({
        sessionId,
        userId,
        text: text.slice(0, 1000),
        status: 'pending' as const,
        position: i,
      })),
    );
  });
}

/**
 * Mark every todo on a session as done. finalize_deep_research calls this
 * after the wiki page is persisted — the workflow is complete.
 */
export async function markAllTodosDone(sessionId: string): Promise<void> {
  await db
    .update(agentTodos)
    .set({ status: 'done', updatedAt: new Date() })
    .where(and(eq(agentTodos.sessionId, sessionId), eq(agentTodos.status, 'pending')));
}

export async function listSessionTodos(sessionId: string, userId: string): Promise<AgentTodo[]> {
  const rows = await db
    .select({
      id: agentTodos.id,
      text: agentTodos.text,
      status: agentTodos.status,
      position: agentTodos.position,
      updatedAt: agentTodos.updatedAt,
    })
    .from(agentTodos)
    .where(and(eq(agentTodos.sessionId, sessionId), eq(agentTodos.userId, userId)))
    .orderBy(asc(agentTodos.position));
  return rows.map((r) => ({
    id: r.id,
    text: r.text,
    status: r.status as 'pending' | 'done',
    position: r.position,
    updatedAt: r.updatedAt,
  }));
}

/**
 * Update a single todo's status. Used by the chat UI when the user manually
 * checks off an item or reopens one. Returns whether the row existed and was
 * owned by the user.
 */
export async function setTodoStatus(
  todoId: string,
  userId: string,
  status: 'pending' | 'done',
): Promise<{ found: boolean }> {
  const rows = await db
    .update(agentTodos)
    .set({ status, updatedAt: sql`now()` })
    .where(and(eq(agentTodos.id, todoId), eq(agentTodos.userId, userId)))
    .returning({ id: agentTodos.id });
  return { found: rows.length > 0 };
}
