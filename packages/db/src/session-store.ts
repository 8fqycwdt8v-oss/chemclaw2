import type { SessionKey, SessionStoreEntry } from '@anthropic-ai/claude-agent-sdk';
import { db } from './client';
import { agentSessions } from './schema/sessions';
import { eq, and, sql, max } from 'drizzle-orm';

/**
 * Returns a session store that forces every key to use the supplied projectKey,
 * preventing one user's sessions from being accessible via another user's context.
 * The SDK derives projectKey from cwd by default; this wrapper overrides that for
 * multi-user server deployments where cwd is shared across all requests.
 */
export function scopedSessionStore(projectKey: string) {
  const scoped = (key: SessionKey): SessionKey => ({ ...key, projectKey });
  return {
    append: (key: SessionKey, entries: SessionStoreEntry[]) =>
      postgresSessionStore.append(scoped(key), entries),
    load: (key: SessionKey) => postgresSessionStore.load(scoped(key)),
    listSessions: () => postgresSessionStore.listSessions(projectKey),
    delete: (key: SessionKey) => postgresSessionStore.delete(scoped(key)),
    listSubkeys: (key: { projectKey: string; sessionId: string }) =>
      postgresSessionStore.listSubkeys({ ...key, projectKey }),
  };
}

export const postgresSessionStore = {
  async append(key: SessionKey, entries: SessionStoreEntry[]): Promise<void> {
    const subpath = key.subpath ?? '';
    const now = Date.now();
    // Wrap in a transaction with SELECT FOR UPDATE to serialize concurrent appends.
    // Without the lock, two concurrent appends with the same base `entries` state
    // both compute `old || new` and the last writer overwrites the other's data.
    await db.transaction(async (tx) => {
      await tx.execute(sql`
        SELECT 1 FROM agent_sessions
        WHERE project_key = ${key.projectKey}
          AND session_id = ${key.sessionId}
          AND subpath = ${subpath}
        FOR UPDATE
      `);
      await tx
        .insert(agentSessions)
        .values({
          projectKey: key.projectKey,
          sessionId: key.sessionId,
          subpath,
          entries,
          mtime: now,
        })
        .onConflictDoUpdate({
          target: [agentSessions.projectKey, agentSessions.sessionId, agentSessions.subpath],
          set: {
            entries: sql`${agentSessions.entries} || excluded.entries`,
            mtime: sql`GREATEST(${agentSessions.mtime}, excluded.mtime)`,
          },
        });
    });
  },

  async load(key: SessionKey): Promise<SessionStoreEntry[] | null> {
    const subpath = key.subpath ?? '';
    const row = await db.query.agentSessions.findFirst({
      where: and(
        eq(agentSessions.projectKey, key.projectKey),
        eq(agentSessions.sessionId, key.sessionId),
        eq(agentSessions.subpath, subpath),
      ),
    });
    return row ? (row.entries as SessionStoreEntry[]) : null;
  },

  async listSessions(projectKey: string): Promise<Array<{ sessionId: string; mtime: number }>> {
    const rows = await db
      .select({
        sessionId: agentSessions.sessionId,
        mtime: max(agentSessions.mtime).as('mtime'),
      })
      .from(agentSessions)
      .where(eq(agentSessions.projectKey, projectKey))
      .groupBy(agentSessions.sessionId);
    return rows
      .filter((r) => r.mtime !== null)
      .map((r) => ({ sessionId: r.sessionId, mtime: r.mtime as number }));
  },

  async delete(key: SessionKey): Promise<void> {
    const subpath = key.subpath ?? '';
    if (subpath === '') {
      // Delete main key + all subkeys for this session
      await db
        .delete(agentSessions)
        .where(
          and(
            eq(agentSessions.projectKey, key.projectKey),
            eq(agentSessions.sessionId, key.sessionId),
          ),
        );
    } else {
      await db
        .delete(agentSessions)
        .where(
          and(
            eq(agentSessions.projectKey, key.projectKey),
            eq(agentSessions.sessionId, key.sessionId),
            eq(agentSessions.subpath, subpath),
          ),
        );
    }
  },

  async listSubkeys(key: { projectKey: string; sessionId: string }): Promise<string[]> {
    const rows = await db
      .select({ subpath: agentSessions.subpath })
      .from(agentSessions)
      .where(
        and(
          eq(agentSessions.projectKey, key.projectKey),
          eq(agentSessions.sessionId, key.sessionId),
        ),
      );
    return rows.map((r) => r.subpath).filter((s) => s !== '');
  },
} satisfies {
  append(key: SessionKey, entries: SessionStoreEntry[]): Promise<void>;
  load(key: SessionKey): Promise<SessionStoreEntry[] | null>;
  listSessions(projectKey: string): Promise<Array<{ sessionId: string; mtime: number }>>;
  delete(key: SessionKey): Promise<void>;
  listSubkeys(key: { projectKey: string; sessionId: string }): Promise<string[]>;
};
