import { eq, sql, and, lt, notInArray, desc } from 'drizzle-orm';
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

export async function updateCampaignStatus(
  id: string,
  status: string,
  plan?: Record<string, unknown>,
): Promise<void> {
  await db
    .update(synthesisCampaigns)
    .set({ status, ...(plan ? { plan } : {}) })
    .where(eq(synthesisCampaigns.id, id));
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
    .where(and(eq(synthesisCampaigns.id, id), eq(synthesisCampaigns.createdBy, userId)))
    .returning({ id: synthesisCampaigns.id });
  return { found: rows.length > 0 };
}

export async function getCampaignBySession(sessionId: string) {
  const [row] = await db
    .select()
    .from(synthesisCampaigns)
    .where(and(
      eq(synthesisCampaigns.sessionId, sessionId),
      notInArray(synthesisCampaigns.status, ['complete', 'failed']),
    ))
    .orderBy(desc(synthesisCampaigns.createdAt))
    .limit(1);
  return row ?? null;
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
  const backoffMinutes = Math.pow(2, retryCount); // 1, 2, 4 minutes
  await db
    .update(campaignSteps)
    .set({
      status: 'failed',
      retryCount: retryCount + 1,
      // Parameterized interval — avoids sql.raw() with NaN/Infinity risk
      nextRetryAt: sql`NOW() + (${backoffMinutes} * INTERVAL '1 minute')`,
    })
    .where(eq(campaignSteps.id, id));
}

export async function markStepComplete(id: string, result: Record<string, unknown>): Promise<void> {
  await db
    .update(campaignSteps)
    .set({ status: 'complete', result })
    .where(eq(campaignSteps.id, id));
}
