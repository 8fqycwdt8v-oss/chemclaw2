import { db } from './client.js';
import { agentSessions } from './schema/sessions.js';
import { eq, and, sql } from 'drizzle-orm';

// We import the type only — the SDK is a peer dep
type SessionStoreEntry = Record<string, unknown>;

type SessionKey = {
  projectKey: string;
  sessionId: string;
  subpath?: string;
};

export const postgresSessionStore = {
  async append(key: SessionKey, entries: SessionStoreEntry[]): Promise<void> {
    const subpath = key.subpath ?? '';
    const now = Date.now();
    await db
      .insert(agentSessions)
      .values({
        projectKey: key.projectKey,
        sessionId: key.sessionId,
        subpath,
        entries: entries as any,
        mtime: now,
      })
      .onConflictDoUpdate({
        target: [agentSessions.projectKey, agentSessions.sessionId, agentSessions.subpath],
        set: {
          entries: sql`agent_sessions.entries || ${JSON.stringify(entries)}::jsonb`,
          mtime: now,
        },
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
      .selectDistinct({ sessionId: agentSessions.sessionId, mtime: agentSessions.mtime })
      .from(agentSessions)
      .where(eq(agentSessions.projectKey, projectKey));
    return rows;
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
  append: (key: SessionKey, entries: SessionStoreEntry[]) => Promise<void>;
  load: (key: SessionKey) => Promise<SessionStoreEntry[] | null>;
  listSessions: (projectKey: string) => Promise<Array<{ sessionId: string; mtime: number }>>;
  delete: (key: SessionKey) => Promise<void>;
  listSubkeys: (key: { projectKey: string; sessionId: string }) => Promise<string[]>;
};
