import { eq, and, inArray } from 'drizzle-orm';
import { db } from '../client';
import { toolPermissions } from '../schema/tool-permissions';

export type ToolMode = 'allow' | 'ask' | 'deny';

/**
 * Resolve the effective mode for (userId, projectId?, toolName).
 * Precedence: user > project > org > default 'allow'.
 *
 * One query returns all matching rows and the helper picks the highest-priority
 * scope. Keeps the agent's canUseTool callback to a single DB round-trip per
 * invocation.
 */
export async function resolveToolMode(
  toolName: string,
  userId: string,
  projectId?: string,
): Promise<ToolMode> {
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

  return (byScope.user as ToolMode | undefined)
    ?? (byScope.project as ToolMode | undefined)
    ?? (byScope.org as ToolMode | undefined)
    ?? 'allow';
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
}
