import { db, sql } from '@chemclaw2/db';

const BACKLOG_WARN_THRESHOLD = 500;

/**
 * /api/health returns 200 even on partial degradation — Fly's health check
 * only watches HTTP status. Component-level state is in the JSON body so
 * dashboards/alerting can distinguish "web up, worker behind" from "all green".
 */
export async function GET() {
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
  } catch {
    dbOk = false;
  }

  const fpBacklog = pendingCompounds + pendingReactions;
  return Response.json({
    ok: true,
    db: dbOk,
    fingerprint_backlog: { compounds: pendingCompounds, reactions: pendingReactions },
    worker_warn: fpBacklog > BACKLOG_WARN_THRESHOLD,
  });
}
