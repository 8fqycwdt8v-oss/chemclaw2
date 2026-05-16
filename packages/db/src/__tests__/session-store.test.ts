import { describe, it, expect, beforeAll, afterAll } from 'vitest';
import type { SessionStoreEntry } from '@anthropic-ai/claude-agent-sdk';
import { postgresSessionStore } from '../session-store';
import { db } from '../client';
import { agentSessions } from '../schema/sessions';
import { and, eq } from 'drizzle-orm';

const TEST_PROJECT = 'test-project';
const TEST_SESSION = `test-session-${Date.now()}`;

// Skip entire suite if DATABASE_URL is not configured (local dev without Postgres)
const skip = !process.env.DATABASE_URL;

function entry(role: string, content: string): SessionStoreEntry {
  return { type: 'message', role, content };
}

async function cleanup() {
  await db.delete(agentSessions).where(
    and(
      eq(agentSessions.projectKey, TEST_PROJECT),
      eq(agentSessions.sessionId, TEST_SESSION),
    ),
  );
}

describe.skipIf(skip)('postgresSessionStore', () => {
  beforeAll(cleanup);
  afterAll(cleanup);

  it('append: accumulates entries across multiple calls', async () => {
    const key = { projectKey: TEST_PROJECT, sessionId: TEST_SESSION };

    await postgresSessionStore.append(key, [entry('user', 'hello')]);
    await postgresSessionStore.append(key, [entry('assistant', 'hi')]);

    const result = await postgresSessionStore.load(key);
    expect(result).toHaveLength(2);
    expect(result![0]).toMatchObject({ role: 'user', content: 'hello' });
    expect(result![1]).toMatchObject({ role: 'assistant', content: 'hi' });
  });

  it('load: returns null for unknown session', async () => {
    const result = await postgresSessionStore.load({
      projectKey: TEST_PROJECT,
      sessionId: 'nonexistent-' + Date.now(),
    });
    expect(result).toBeNull();
  });

  it('append + load: subkey stored and retrieved independently of main key', async () => {
    const mainKey = { projectKey: TEST_PROJECT, sessionId: TEST_SESSION };
    const subKey = { projectKey: TEST_PROJECT, sessionId: TEST_SESSION, subpath: 'sub1' };

    await postgresSessionStore.append(subKey, [entry('system', 'context')]);

    const main = await postgresSessionStore.load(mainKey);
    const sub = await postgresSessionStore.load(subKey);

    // Main key entries should not include the subkey entry
    const mainContents = (main ?? []).map((e) => (e as { content?: unknown }).content);
    expect(mainContents).not.toContain('context');
    expect(sub).toHaveLength(1);
    expect(sub![0]).toMatchObject({ role: 'system', content: 'context' });
  });

  it('listSubkeys: returns subpaths for the session, excludes main key', async () => {
    const subkeys = await postgresSessionStore.listSubkeys({
      projectKey: TEST_PROJECT,
      sessionId: TEST_SESSION,
    });
    expect(subkeys).toContain('sub1');
    expect(subkeys).not.toContain(''); // main key subpath is '' — must be excluded
  });

  it('delete(subkey): removes only the subpath, leaves main key intact', async () => {
    const mainKey = { projectKey: TEST_PROJECT, sessionId: TEST_SESSION };
    const subKey = { projectKey: TEST_PROJECT, sessionId: TEST_SESSION, subpath: 'sub1' };

    await postgresSessionStore.delete(subKey);

    expect(await postgresSessionStore.load(subKey)).toBeNull();
    expect(await postgresSessionStore.load(mainKey)).not.toBeNull();
  });

  it('delete(main key): cascade removes main key and all remaining subkeys', async () => {
    const subKey2 = { projectKey: TEST_PROJECT, sessionId: TEST_SESSION, subpath: 'sub2' };
    await postgresSessionStore.append(subKey2, [entry('user', 'sub2')]);

    const mainKey = { projectKey: TEST_PROJECT, sessionId: TEST_SESSION };
    await postgresSessionStore.delete(mainKey);

    expect(await postgresSessionStore.load(mainKey)).toBeNull();
    expect(await postgresSessionStore.load(subKey2)).toBeNull();
  });

  it('append: rejects pathologically long key components', async () => {
    const longKey = 'x'.repeat(257);
    await expect(
      postgresSessionStore.append({ projectKey: longKey, sessionId: TEST_SESSION }, [entry('user', 'hi')]),
    ).rejects.toThrow(/projectKey exceeds/);
  });

  it('append: rejects too many entries in a single call', async () => {
    const key = { projectKey: TEST_PROJECT, sessionId: `cap-${Date.now()}` };
    const tooMany = Array.from({ length: 101 }, (_, i) => entry('user', `e${i}`));
    await expect(postgresSessionStore.append(key, tooMany)).rejects.toThrow(/refusing to append/);
  });
});
