import type { SessionKey, SessionStoreEntry } from '@anthropic-ai/claude-agent-sdk';
import { withSpan } from '@chemclaw2/observability';
import { db } from './client';
import { agentSessions } from './schema/sessions';
import { eq, and, sql, max } from 'drizzle-orm';

// Caps protect against (a) advisory-lock keys derived from pathological inputs
// and (b) OOM when the SDK keeps appending forever. Realistic sessions stay
// well under both limits.
const MAX_KEY_PART_LEN = 256;
const MAX_APPEND_ENTRIES = 100;
const MAX_ENTRY_SERIALIZED_BYTES = 1_000_000;

function assertKeyComponent(name: string, value: string): void {
  if (value.length > MAX_KEY_PART_LEN) {
    throw new Error(`session-store: ${name} exceeds ${MAX_KEY_PART_LEN} chars`);
  }
}

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
    assertKeyComponent('projectKey', key.projectKey);
    assertKeyComponent('sessionId', key.sessionId);
    assertKeyComponent('subpath', subpath);
    if (entries.length > MAX_APPEND_ENTRIES) {
      throw new Error(`session-store: refusing to append ${entries.length} entries (max ${MAX_APPEND_ENTRIES})`);
    }
    const serializedSize = Buffer.byteLength(JSON.stringify(entries), 'utf8');
    if (serializedSize > MAX_ENTRY_SERIALIZED_BYTES) {
      throw new Error(`session-store: entries serialize to ${serializedSize} bytes (max ${MAX_ENTRY_SERIALIZED_BYTES})`);
    }
    const now = Date.now();
    // Serialize concurrent appends with a transaction-scoped advisory lock.
    // The two-arg form composes 64 bits from two 32-bit hashes — collisions
    // between independent (projectKey, sessionId+subpath) pairs only occur if
    // BOTH halves hash-collide, vs. ~1e-9 per single-key hashtext alone.
    //
    // Span carries the advisory-lock + insert path so contention spikes (the
    // common cause of agent-stream latency tails) get attributed correctly.
    await withSpan(
      'session_store.append',
      { entries_count: entries.length, serialized_bytes: serializedSize },
      async () => {
        await db.transaction(async (tx) => {
          await tx.execute(sql`
            SELECT pg_advisory_xact_lock(
              hashtext(${key.projectKey}),
              hashtext(${key.sessionId} || '::' || ${subpath})
            )
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
    );
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
