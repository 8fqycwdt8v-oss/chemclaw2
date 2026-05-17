import { db, sql } from '@chemclaw2/db';
import { auth } from '@clerk/nextjs/server';
import { logger } from '@chemclaw2/observability';

const BACKLOG_WARN_THRESHOLD = 500;

/**
 * /api/health is dual-purpose:
 *   - unauthenticated: Fly's TCP/HTTP health check. Returns 503 if DB is
 *     unreachable so a bad rollout fails the check instead of staying up
 *     with a dark backend (audit finding #2).
 *   - authenticated: returns component-level state for the admin
 *     dashboard. Backlog counts are signed-in-only to avoid leaking
 *     compound/reaction registry size to unauthenticated probes.
 */
export async function GET() {
  const { userId } = await auth();

  let dbOk = false;
  let pendingCompounds = 0;
  let pendingReactions = 0;
  try {
    const rows = await db.execute<{
      pending_compounds: number;
      pending_reactions: number;
    }>(sql`
      SELECT
        (SELECT count(*)::int FROM compounds WHERE morgan_fp IS NULL) AS pending_compounds,
        (SELECT count(*)::int FROM reactions WHERE drfp IS NULL) AS pending_reactions
    `);
    if (rows[0]) {
      pendingCompounds = Number(rows[0].pending_compounds ?? 0);
      pendingReactions = Number(rows[0].pending_reactions ?? 0);
    }
    dbOk = true;
  } catch (err) {
    logger.error('health_db_query_failed', {}, err);
    dbOk = false;
  }

  if (!userId) {
    // Probe path — body is intentionally small. Bad rollouts fail the check.
    return Response.json({ ok: dbOk }, { status: dbOk ? 200 : 503 });
  }

  const fpBacklog = pendingCompounds + pendingReactions;
  return Response.json({
    ok: true,
    db: dbOk,
    fingerprint_backlog: { compounds: pendingCompounds, reactions: pendingReactions },
    worker_warn: fpBacklog > BACKLOG_WARN_THRESHOLD,
  });
}
