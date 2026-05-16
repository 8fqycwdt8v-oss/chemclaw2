import { eq, and, inArray } from 'drizzle-orm';
import { db } from '../client';
import { toolPermissions } from '../schema/tool-permissions';

export type ToolMode = 'allow' | 'ask' | 'deny';

// v2.1-A3: process-local cache. Key axes are joined by ASCII Unit Separator
// (\x1f) rather than `:` because legal scope ids in this codebase contain
// colons — the agent's projectKey shape is `chemclaw2:<userId>`. A `:` delimiter
// would make `key.split(':')` return the wrong slices and silently miss the
// invalidation, leaving stale permissions cached. \x1f is reserved for exactly
// this and cannot appear in user / project / tool names.
//
// PreToolUse fires per tool invocation; without caching every tool call hits the
// DB. Entries are invalidated by setToolPermission writes from the admin route
// running in the same process. Multi-process deployments still see at most a
// short staleness window — admins curling the route are not high-frequency.
const KEY_SEP = '\x1f';
const cache = new Map<string, Promise<ToolMode>>();
const cacheKey = (userId: string, projectId: string | undefined, toolName: string) =>
  `${userId}${KEY_SEP}${projectId ?? ''}${KEY_SEP}${toolName}`;

/**
 * Resolve the effective mode for (userId, projectId?, toolName).
 * Precedence: user > project > org > default 'allow'.
 *
 * One query returns all matching rows and the helper picks the highest-priority
 * scope. Result is memoized in-process; setToolPermission clears the relevant
 * keys.
 */
export async function resolveToolMode(
  toolName: string,
  userId: string,
  projectId?: string,
): Promise<ToolMode> {
  const key = cacheKey(userId, projectId, toolName);
  const cached = cache.get(key);
  if (cached) return cached;

  const promise = (async () => {
    const scopeIds: string[] = [userId, 'org'];
    if (projectId) scopeIds.push(projectId);

    const rows = await db
      .select({ scope: toolPermissions.scope, scopeId: toolPermissions.scopeId, mode: toolPermissions.mode })
      .from(toolPermissions)
      .where(and(
        eq(toolPermissions.toolName, toolName),
        inArray(toolPermissions.scopeId, scopeIds),
      ));

    const byScope: Record<string, string> = {};
    for (const r of rows) byScope[r.scope] = r.mode;

    return ((byScope.user as ToolMode | undefined)
      ?? (byScope.project as ToolMode | undefined)
      ?? (byScope.org as ToolMode | undefined)
      ?? 'allow') as ToolMode;
  })();

  cache.set(key, promise);
  // Drop the entry if the resolution rejects so the next call retries the DB
  // instead of serving the rejection forever.
  promise.catch(() => cache.delete(key));
  return promise;
}

/**
 * Clear cached resolutions touching a (scope, scopeId, toolName) write. The
 * scope tells us which axis of the cache key the write affects:
 *  - 'user'    invalidates entries for that user × toolName
 *  - 'project' invalidates entries for that projectId × toolName
 *  - 'org'     invalidates every entry for that toolName (org applies everywhere)
 */
function invalidateCache(scope: 'user' | 'project' | 'org', scopeId: string, toolName: string) {
  for (const key of cache.keys()) {
    const [u, p, t] = key.split(KEY_SEP);
    if (t !== toolName) continue;
    if (scope === 'org') { cache.delete(key); continue; }
    if (scope === 'user' && u === scopeId) cache.delete(key);
    if (scope === 'project' && p === scopeId) cache.delete(key);
  }
}

export async function setToolPermission(
  scope: 'user' | 'project' | 'org',
  scopeId: string,
  toolName: string,
  mode: ToolMode,
  updatedBy: string,
): Promise<void> {
  await db
    .insert(toolPermissions)
    .values({ scope, scopeId, toolName, mode, updatedBy })
    .onConflictDoUpdate({
      target: [toolPermissions.scope, toolPermissions.scopeId, toolPermissions.toolName],
      set: { mode, updatedBy, updatedAt: new Date() },
    });
  invalidateCache(scope, scopeId, toolName);
}

/**
 * Test seam — drops every cached resolution. Production code does not call this;
 * the test suite uses it between cases to avoid bleed-through.
 */
export function __resetToolModeCacheForTests(): void {
  cache.clear();
}
