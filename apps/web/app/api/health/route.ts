import { auth } from '@clerk/nextjs/server';
import { countPendingFingerprints } from '@chemclaw2/db';

const BACKLOG_WARN_THRESHOLD = 500;

/**
 * /api/health returns 200 even on partial degradation — Fly's health check
 * only watches HTTP status. Component-level state is in the JSON body so
 * dashboards/alerting can distinguish "web up, worker behind" from "all green".
 *
 * Backlog counts are signed-in-only to avoid leaking compound/reaction
 * registry size to unauthenticated probes.
 */
export async function GET() {
  const { userId } = await auth();
  if (!userId) return Response.json({ ok: true });

  let dbOk = false;
  let pendingCompounds = 0;
  let pendingReactions = 0;
  try {
    const counts = await countPendingFingerprints();
    pendingCompounds = counts.pendingCompounds;
    pendingReactions = counts.pendingReactions;
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
