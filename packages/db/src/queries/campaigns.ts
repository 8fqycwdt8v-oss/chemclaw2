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
  if (!row) throw new Error('createCampaign: insert returned no row');
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
  // Idempotent insert: if (campaign_id, step_idx) already exists, return the
  // existing row id instead of throwing. Matches the UNIQUE constraint added
  // in migration 0031 — concurrent confirm_synthesis_plan retries are safe.
  const [row] = await db
    .insert(campaignSteps)
    .values({ campaignId, stepIdx, ...opts })
    .onConflictDoNothing({ target: [campaignSteps.campaignId, campaignSteps.stepIdx] })
    .returning({ id: campaignSteps.id });
  if (row) return row.id;
  const [existing] = await db
    .select({ id: campaignSteps.id })
    .from(campaignSteps)
    .where(and(eq(campaignSteps.campaignId, campaignId), eq(campaignSteps.stepIdx, stepIdx)));
  if (!existing) {
    throw new Error(`addCampaignStep: insert no-op but row not found for (${campaignId}, ${stepIdx})`);
  }
  return existing.id;
}

/**
 * Atomic status-flip + step-insert for confirm_synthesis_plan. Without this
 * the status flips to 'awaiting_input' first, then steps insert in a loop;
 * a failure partway through the loop leaves the campaign wedged in
 * awaiting_input with only the steps that landed before the throw.
 *
 * Returns { found: false } if the campaign is missing, not owned by userId,
 * or already terminal. On any insert failure inside the transaction the
 * whole change rolls back, restoring the prior status.
 */
export async function confirmCampaignPlanForUser(
  campaignId: string,
  userId: string,
  plan: Record<string, unknown>,
  steps: Array<{ reactionSmiles?: string; conditions?: string }>,
): Promise<{ found: boolean }> {
  return db.transaction(async (tx) => {
    const updated = await tx
      .update(synthesisCampaigns)
      .set({ status: 'awaiting_input', plan })
      .where(and(
        eq(synthesisCampaigns.id, campaignId),
        eq(synthesisCampaigns.createdBy, userId),
        inArray(synthesisCampaigns.status, [...NON_TERMINAL_STATUSES]),
      ))
      .returning({ id: synthesisCampaigns.id });
    if (updated.length === 0) return { found: false };
    for (let i = 0; i < steps.length; i++) {
      const s = steps[i];
      await tx
        .insert(campaignSteps)
        .values({ campaignId, stepIdx: i, ...s })
        .onConflictDoNothing({ target: [campaignSteps.campaignId, campaignSteps.stepIdx] });
    }
    return { found: true };
  });
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
  // Clamp the previous count to [0, 9] so the written value (clamped + 1)
  // never exceeds the CHECK constraint (retry_count ≤ 10) and the backoff
  // stays bounded (2^9 = 512 minutes ≈ 8.5h max).
  const clamped = Math.min(Math.max(retryCount, 0), 9);
  const backoffMinutes = Math.pow(2, clamped);
  // Terminal-state guard: a step that's already 'complete' must not be
  // flipped to 'failed' because a downstream bookkeeping call threw after
  // the step itself finished. Mirrors the guard in markStepComplete.
  await db
    .update(campaignSteps)
    .set({
      status: 'failed',
      retryCount: clamped + 1,
      // Parameterized interval — avoids sql.raw() with NaN/Infinity risk
      nextRetryAt: sql`NOW() + (${backoffMinutes} * INTERVAL '1 minute')`,
    })
    .where(and(eq(campaignSteps.id, id), notInArray(campaignSteps.status, ['complete', 'failed'])));
}

export async function markStepComplete(id: string, result: Record<string, unknown>): Promise<void> {
  // Refuse to leave a terminal state: a late-arriving success from a re-tried
  // job must not overwrite a 'failed' step the user already saw.
  await db
    .update(campaignSteps)
    .set({ status: 'complete', result })
    .where(and(eq(campaignSteps.id, id), notInArray(campaignSteps.status, ['complete', 'failed'])));
}

export type PendingCampaignNotification = {
  id: string;
  targetSmiles: string | null;
  status: string;
  wikiPageId: string | null;
  updatedAt: Date;
};

/**
 * Terminal-state campaigns owned by the user that have not yet been
 * acknowledged via `acknowledgeCampaignNotifications`. Source for the
 * notifications page + nav badge.
 */
export async function listPendingCampaignNotifications(
  userId: string,
): Promise<PendingCampaignNotification[]> {
  const rows = await db
    .select({
      id: synthesisCampaigns.id,
      targetSmiles: synthesisCampaigns.targetSmiles,
      status: synthesisCampaigns.status,
      wikiPageId: synthesisCampaigns.wikiPageId,
      updatedAt: synthesisCampaigns.updatedAt,
    })
    .from(synthesisCampaigns)
    .where(and(
      eq(synthesisCampaigns.createdBy, userId),
      inArray(synthesisCampaigns.status, ['complete', 'failed']),
      sql`${synthesisCampaigns.notifiedAt} IS NULL`,
    ));
  return rows;
}

/**
 * Mark a batch of campaigns as acknowledged. Ownership-scoped: only the
 * authoring user's rows are flipped, even if an attacker supplies a UUID
 * they don't own. Returns the ids actually updated.
 */
export async function acknowledgeCampaignNotifications(
  userId: string,
  campaignIds: string[],
): Promise<string[]> {
  if (campaignIds.length === 0) return [];
  const rows = await db
    .update(synthesisCampaigns)
    .set({ notifiedAt: new Date() })
    .where(and(
      eq(synthesisCampaigns.createdBy, userId),
      inArray(synthesisCampaigns.id, campaignIds),
      sql`${synthesisCampaigns.notifiedAt} IS NULL`,
    ))
    .returning({ id: synthesisCampaigns.id });
  return rows.map((r) => r.id);
}

/**
 * Approve one pending step on a user-owned campaign. Atomic:
 * - flips `requires_approval=false` and bumps `next_retry_at=NOW()` so the
 *   worker picks it up on the next sweep
 * - only matches rows where (campaign-owned-by-user, step still pending,
 *   approval still required) — idempotent on re-call
 * Returns whether a row was actually flipped.
 */
export async function approveCampaignStep(
  campaignId: string,
  userId: string,
  stepIdx: number,
): Promise<{ approved: boolean }> {
  const rows = await db.execute<{ id: string }>(sql`
    UPDATE campaign_steps cs
       SET requires_approval = false, next_retry_at = NOW()
      FROM synthesis_campaigns sc
     WHERE cs.campaign_id = sc.id
       AND sc.id = ${campaignId}::uuid
       AND sc.created_by = ${userId}
       AND cs.step_idx = ${stepIdx}
       AND cs.requires_approval = true
       AND cs.status = 'pending'
    RETURNING cs.id
  `);
  return { approved: rows.length > 0 };
}

/**
 * Kick off (or no-op) pending steps when a campaign transitions to `running`.
 * Optionally gate every non-first step on per-step approval. Used by
 * `kickoff_campaign` so the agent-tool layer no longer reaches for raw SQL.
 *
 * `userId` scoping is defence-in-depth: the caller already enforces ownership
 * via `updateCampaignStatusForUser`, but writing the gating UPDATEs through a
 * `created_by = userId` predicate keeps a misuse from racing across users.
 */
export async function startPendingStepsForUser(
  campaignId: string,
  userId: string,
  opts: { perStepApproval: boolean },
): Promise<void> {
  await db.execute(sql`
    UPDATE campaign_steps SET next_retry_at = NOW()
     WHERE campaign_id = ${campaignId}::uuid
       AND status = 'pending'
       AND campaign_id IN (SELECT id FROM synthesis_campaigns WHERE created_by = ${userId})
  `);
  if (opts.perStepApproval) {
    await db.execute(sql`
      UPDATE campaign_steps SET requires_approval = true
       WHERE campaign_id = ${campaignId}::uuid
         AND step_idx > 0
         AND campaign_id IN (SELECT id FROM synthesis_campaigns WHERE created_by = ${userId})
    `);
  }
}
