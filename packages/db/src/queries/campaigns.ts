import { eq, sql, and, lt, notInArray, desc, asc, inArray } from 'drizzle-orm';
import { db } from '../client';
import { synthesisCampaigns, campaignSteps } from '../schema/campaigns';

export async function createCampaign(
  sessionId: string,
  createdBy: string,
  targetSmiles?: string,
): Promise<string> {
  const [row] = await db
    .insert(synthesisCampaigns)
    .values({ sessionId, createdBy, targetSmiles })
    .returning({ id: synthesisCampaigns.id });
  return row.id;
}

export const TERMINAL_STATUSES = ['complete', 'failed'] as const;
export const NON_TERMINAL_STATUSES = ['planning', 'awaiting_input', 'running'] as const;

export async function updateCampaignStatus(
  id: string,
  status: string,
  plan?: Record<string, unknown>,
): Promise<void> {
  // Guard: never transition out of a terminal state (complete or failed)
  await db
    .update(synthesisCampaigns)
    .set({ status, ...(plan ? { plan } : {}) })
    .where(and(eq(synthesisCampaigns.id, id), inArray(synthesisCampaigns.status, [...NON_TERMINAL_STATUSES])));
}

export async function updateCampaignStatusForUser(
  id: string,
  userId: string,
  status: string,
  plan?: Record<string, unknown>,
): Promise<{ found: boolean }> {
  const rows = await db
    .update(synthesisCampaigns)
    .set({ status, ...(plan ? { plan } : {}) })
    .where(and(
      eq(synthesisCampaigns.id, id),
      eq(synthesisCampaigns.createdBy, userId),
      inArray(synthesisCampaigns.status, [...NON_TERMINAL_STATUSES]),
    ))
    .returning({ id: synthesisCampaigns.id });
  return { found: rows.length > 0 };
}

// userId is required: prevents an attacker who guesses or knows another
// user's session UUID from reading their active campaign (cross-tenant IDOR
// disclosure of target_smiles + status).
export async function getCampaignBySession(sessionId: string, userId: string) {
  const [row] = await db
    .select()
    .from(synthesisCampaigns)
    .where(and(
      eq(synthesisCampaigns.sessionId, sessionId),
      eq(synthesisCampaigns.createdBy, userId),
      notInArray(synthesisCampaigns.status, [...TERMINAL_STATUSES]),
    ))
    .orderBy(desc(synthesisCampaigns.createdAt))
    .limit(1);
  return row ?? null;
}

/**
 * Owner-scoped fetch of one campaign + its steps in stepIdx order. Used by
 * kickoff_campaign to seed agent_todos with one entry per step so the user
 * sees the campaign's checklist surface alongside deep-research todos.
 */
export async function getCampaignWithStepsForUser(campaignId: string, userId: string) {
  const [campaign] = await db
    .select()
    .from(synthesisCampaigns)
    .where(and(
      eq(synthesisCampaigns.id, campaignId),
      eq(synthesisCampaigns.createdBy, userId),
    ))
    .limit(1);
  if (!campaign) return null;
  const steps = await db
    .select()
    .from(campaignSteps)
    .where(eq(campaignSteps.campaignId, campaignId))
    .orderBy(asc(campaignSteps.stepIdx));
  return { campaign, steps };
}

export async function addCampaignStep(
  campaignId: string,
  stepIdx: number,
  opts?: { reactionSmiles?: string; conditions?: string },
): Promise<string> {
  const [row] = await db
    .insert(campaignSteps)
    .values({ campaignId, stepIdx, ...opts })
    .returning({ id: campaignSteps.id });
  return row.id;
}

export async function getStepsForRetry(): Promise<Array<typeof campaignSteps.$inferSelect>> {
  return db
    .select()
    .from(campaignSteps)
    .where(
      and(
        eq(campaignSteps.status, 'failed'),
        lt(campaignSteps.retryCount, 3),
        sql`next_retry_at <= NOW()`,
      ),
    );
}

export async function markStepFailed(id: string, retryCount: number): Promise<void> {
  // Clamp defends against corrupt rows: a retry_count of 30 would yield
  // 2^30 ≈ 17h backoff and re-trip the schedule check on every sweep.
  const clamped = Math.min(Math.max(retryCount, 0), 10);
  const backoffMinutes = Math.pow(2, clamped); // 1, 2, 4 ... 1024 minutes
  await db
    .update(campaignSteps)
    .set({
      status: 'failed',
      retryCount: clamped + 1,
      // Parameterized interval — avoids sql.raw() with NaN/Infinity risk
      nextRetryAt: sql`NOW() + (${backoffMinutes} * INTERVAL '1 minute')`,
    })
    .where(eq(campaignSteps.id, id));
}

export async function markStepComplete(id: string, result: Record<string, unknown>): Promise<void> {
  // Refuse to leave a terminal state: a late-arriving success from a re-tried
  // job must not overwrite a 'failed' step the user already saw.
  await db
    .update(campaignSteps)
    .set({ status: 'complete', result })
    .where(and(eq(campaignSteps.id, id), notInArray(campaignSteps.status, ['complete', 'failed'])));
}
