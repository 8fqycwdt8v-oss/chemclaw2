import { and, eq } from 'drizzle-orm';
import { db } from '../client';
import { agentSessions } from '../schema/sessions';

export interface SessionEntry {
  role: string;
  content: unknown;
  timestamp?: number;
}

export async function replaySession(
  sessionId: string,
  projectKey: string,
): Promise<SessionEntry[]> {
  const rows = await db
    .select({ entries: agentSessions.entries })
    .from(agentSessions)
    .where(and(
      eq(agentSessions.projectKey, projectKey),
      eq(agentSessions.sessionId, sessionId),
      eq(agentSessions.subpath, ''),
    ))
    .orderBy(agentSessions.mtime);

  return rows.flatMap((r) => {
    const entries = r.entries as SessionEntry[] | null;
    return entries ?? [];
  });
}
