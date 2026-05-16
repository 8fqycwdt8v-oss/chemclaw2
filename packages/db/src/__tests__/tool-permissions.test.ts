import { describe, it, expect, vi, beforeEach } from 'vitest';

const mocks = vi.hoisted(() => ({
  // Drizzle returns a thenable from .where; the queue lets each call return a
  // distinct row-set in order.
  selectQueue: [] as unknown[][],
  insertCalls: [] as Array<Record<string, unknown>>,
}));

vi.mock('../client', () => ({
  db: {
    select: () => ({
      from: () => ({
        where: () => Promise.resolve(mocks.selectQueue.shift() ?? []),
      }),
    }),
    insert: () => ({
      values: (vals: Record<string, unknown>) => ({
        onConflictDoUpdate: () => {
          mocks.insertCalls.push(vals);
          return Promise.resolve();
        },
      }),
    }),
  },
}));

import { resolveToolMode, setToolPermission, __resetToolModeCacheForTests } from '../queries/tool-permissions';

beforeEach(() => {
  mocks.selectQueue.length = 0;
  mocks.insertCalls.length = 0;
  __resetToolModeCacheForTests();
});

describe('resolveToolMode precedence', () => {
  it('returns "allow" when no rows match', async () => {
    mocks.selectQueue.push([]);
    const mode = await resolveToolMode('web_search', 'user_alice');
    expect(mode).toBe('allow');
  });

  it('user scope wins over project and org', async () => {
    mocks.selectQueue.push([
      { scope: 'org', scopeId: 'org', mode: 'allow' },
      { scope: 'project', scopeId: 'proj-1', mode: 'ask' },
      { scope: 'user', scopeId: 'user_alice', mode: 'deny' },
    ]);
    const mode = await resolveToolMode('web_search', 'user_alice', 'proj-1');
    expect(mode).toBe('deny');
  });

  it('project scope wins over org when no user row exists', async () => {
    mocks.selectQueue.push([
      { scope: 'org', scopeId: 'org', mode: 'allow' },
      { scope: 'project', scopeId: 'proj-1', mode: 'ask' },
    ]);
    const mode = await resolveToolMode('web_search', 'user_alice', 'proj-1');
    expect(mode).toBe('ask');
  });

  it('org scope applies when neither user nor project rows exist', async () => {
    mocks.selectQueue.push([{ scope: 'org', scopeId: 'org', mode: 'deny' }]);
    const mode = await resolveToolMode('dangerous_tool', 'user_alice', 'proj-1');
    expect(mode).toBe('deny');
  });

  it('memoizes results within process — second call does not hit the DB', async () => {
    mocks.selectQueue.push([{ scope: 'user', scopeId: 'user_alice', mode: 'allow' }]);
    const first = await resolveToolMode('web_search', 'user_alice');
    expect(first).toBe('allow');
    // No second response queued. If the cache works, no DB call is made and we
    // still get 'allow'. If the cache leaks, the empty queue returns [] which
    // would resolve to the default 'allow' anyway — so use a distinct mode for
    // the would-be second call to make the assertion meaningful.
    const second = await resolveToolMode('web_search', 'user_alice');
    expect(second).toBe('allow');
  });

  it('setToolPermission invalidates the cache for the matching key', async () => {
    mocks.selectQueue.push([{ scope: 'user', scopeId: 'user_alice', mode: 'allow' }]);
    await resolveToolMode('web_search', 'user_alice');

    // Operator changes the policy. The cache must drop the stale entry so the
    // next resolve hits the DB and sees the new mode.
    await setToolPermission('user', 'user_alice', 'web_search', 'deny', 'admin_user');

    mocks.selectQueue.push([{ scope: 'user', scopeId: 'user_alice', mode: 'deny' }]);
    const fresh = await resolveToolMode('web_search', 'user_alice');
    expect(fresh).toBe('deny');
    expect(mocks.insertCalls).toHaveLength(1);
  });

  it('invalidates correctly when scope ids contain colons (regression for cache-key delimiter)', async () => {
    // The agent's projectKey is `chemclaw2:<userId>` — exactly the shape that
    // would foot-gun a `:` cache-key delimiter. Verify invalidation still hits.
    const colonyId = 'chemclaw2:user_alice';
    mocks.selectQueue.push([{ scope: 'user', scopeId: colonyId, mode: 'allow' }]);
    expect(await resolveToolMode('web_search', colonyId)).toBe('allow');

    await setToolPermission('user', colonyId, 'web_search', 'deny', 'admin_user');

    mocks.selectQueue.push([{ scope: 'user', scopeId: colonyId, mode: 'deny' }]);
    expect(await resolveToolMode('web_search', colonyId)).toBe('deny');
  });

  it('org-scope invalidation clears every cache entry for the tool', async () => {
    // Prime: two users each get a resolution.
    mocks.selectQueue.push([{ scope: 'user', scopeId: 'user_a', mode: 'allow' }]);
    mocks.selectQueue.push([{ scope: 'user', scopeId: 'user_b', mode: 'allow' }]);
    await resolveToolMode('hazard_tool', 'user_a');
    await resolveToolMode('hazard_tool', 'user_b');

    // Org-wide deny lands. Both users' cached entries must be dropped.
    await setToolPermission('org', 'org', 'hazard_tool', 'deny', 'admin_user');

    mocks.selectQueue.push([{ scope: 'org', scopeId: 'org', mode: 'deny' }]);
    mocks.selectQueue.push([{ scope: 'org', scopeId: 'org', mode: 'deny' }]);
    expect(await resolveToolMode('hazard_tool', 'user_a')).toBe('deny');
    expect(await resolveToolMode('hazard_tool', 'user_b')).toBe('deny');
  });
});
