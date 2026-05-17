import { createHash } from 'node:crypto';
import { and, eq } from 'drizzle-orm';
import { db } from '../client';
import { agentFeedback, agentOverrides } from '../schema/feedback';
import { agentSessions } from '../schema/sessions';

/**
 * Verify a session belongs to a given user. Sessions are keyed by
 * (project_key = chemclaw2:<userId>, session_id, subpath). Used to gate
 * feedback writes — without it any signed-in user could attach feedback
 * rows to any sessionId.
 */
export async function sessionBelongsToUser(sessionId: string, userId: string): Promise<boolean> {
  const projectKey = `chemclaw2:${userId}`;
  const rows = await db
    .select({ sessionId: agentSessions.sessionId })
    .from(agentSessions)
    .where(and(eq(agentSessions.projectKey, projectKey), eq(agentSessions.sessionId, sessionId)))
    .limit(1);
  return rows.length > 0;
}

/**
 * Upsert one feedback row per (session, turn, user). Re-submitting overwrites
 * the previous score/reason. Score must be -1 or +1.
 */
export async function upsertFeedback(
  sessionId: string,
  turnIndex: number,
  userId: string,
  score: -1 | 1,
  reason: string | null,
): Promise<{ id: string }> {
  const [row] = await db
    .insert(agentFeedback)
    .values({ sessionId, turnIndex, userId, score, reason })
    .onConflictDoUpdate({
      target: [agentFeedback.sessionId, agentFeedback.turnIndex, agentFeedback.userId],
      set: { score, reason },
    })
    .returning({ id: agentFeedback.id });
  if (!row) throw new Error('upsertFeedback: insert returned no row');
  return row;
}

/**
 * Record a gate override BEFORE running the agent. promptHash is sha256 of the
 * normalized prompt so post-hoc audit can verify the same prompt was actually
 * run (without storing the prompt itself in plaintext).
 */
export async function recordOverride(
  sessionId: string,
  userId: string,
  gateName: string,
  justification: string,
  promptForHash: string,
): Promise<{ id: string }> {
  const promptHash = createHash('sha256').update(promptForHash).digest('hex');
  const [row] = await db
    .insert(agentOverrides)
    .values({ sessionId, userId, gateName, justification, promptHash })
    .returning({ id: agentOverrides.id });
  if (!row) throw new Error('recordOverride: insert returned no row');
  return row;
}
