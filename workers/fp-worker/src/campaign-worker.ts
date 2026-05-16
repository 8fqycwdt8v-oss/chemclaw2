import PgBoss from 'pg-boss';
import OpenAI from 'openai';
import { ne, eq, and, lt, inArray, notInArray, sql } from 'drizzle-orm';
import { db } from '@chemclaw2/db';
import { campaignSteps, synthesisCampaigns } from '@chemclaw2/db';
import { upsertWikiPage } from '@chemclaw2/db';
import {
  getStepsForRetry,
  markStepFailed,
  markStepComplete,
  TERMINAL_STATUSES,
} from '@chemclaw2/db';

// Wiki auto-creation uses OpenAI text-embedding-3-small, same model as the web app
let openaiClient: OpenAI | undefined;
function getOpenAI(): OpenAI {
  if (!openaiClient) {
    const apiKey = process.env.OPENAI_API_KEY;
    if (!apiKey) throw new Error('OPENAI_API_KEY required for campaign wiki page creation');
    openaiClient = new OpenAI({ apiKey });
  }
  return openaiClient;
}

async function embedTextsForWorker(texts: string[]): Promise<number[][]> {
  const res = await getOpenAI().embeddings.create({
    model: 'text-embedding-3-small',
    input: texts.map((t) => t.slice(0, 6000)),
  });
  return res.data.map((d) => d.embedding);
}

// Campaigns that stay 'running' for > 30 minutes are assumed crashed and reset
const DEAD_LETTER_TIMEOUT_MINUTES = 30;

export async function startCampaignWorker(boss: PgBoss): Promise<void> {
  await boss.createQueue('retry-campaign-steps', { policy: PgBoss.policies.standard } as PgBoss.Queue);
  await boss.createQueue('run-campaign-step', { policy: PgBoss.policies.standard } as PgBoss.Queue);
  await boss.createQueue('create-campaign-wiki', { policy: PgBoss.policies.stately } as PgBoss.Queue);

  // Cron every 5 minutes: retry failed steps + sweep dead 'running' steps + pick up pending steps
  await boss.schedule('retry-campaign-steps', '*/5 * * * *');
  await boss.work('retry-campaign-steps', async () => {
    // Enqueue pending steps belonging to campaigns the user has kicked off (status='running').
    // These have status='pending' from confirm_synthesis_plan, but the worker only
    // sweeps failed steps via getStepsForRetry — so without this, kickoff has no effect.
    const pending = await db
      .select({ id: campaignSteps.id })
      .from(campaignSteps)
      .innerJoin(synthesisCampaigns, eq(synthesisCampaigns.id, campaignSteps.campaignId))
      .where(and(
        eq(campaignSteps.status, 'pending'),
        eq(synthesisCampaigns.status, 'running'),
      ))
      .limit(50);
    for (const { id } of pending) {
      await boss.send('run-campaign-step', { stepId: id }, { singletonKey: id }).catch(() => {});
    }

    // Dead-letter sweep: reset steps stuck in 'running' for > DEAD_LETTER_TIMEOUT_MINUTES.
    // Sets next_retry_at = NOW() so getStepsForRetry() picks them up immediately.
    // Guards retry_count < 3 to avoid pushing exhausted steps past the retry cap.
    await db
      .update(campaignSteps)
      .set({ status: 'failed', retryCount: sql`retry_count + 1`, nextRetryAt: sql`NOW()` })
      .where(and(
        eq(campaignSteps.status, 'running'),
        lt(campaignSteps.retryCount, 3),
        sql`updated_at < NOW() - (${DEAD_LETTER_TIMEOUT_MINUTES} * INTERVAL '1 minute')`,
      ));

    const stepsToRetry = await getStepsForRetry();
    for (const { id } of stepsToRetry) {
      await boss.send('run-campaign-step', { stepId: id }, { singletonKey: id }).catch(() => {});
    }
  });

  await boss.work<{ stepId: string }>('run-campaign-step', async (jobs) => {
    for (const job of jobs) {
      const { stepId } = job.data;

      const [claimed] = await db
        .update(campaignSteps)
        .set({ status: 'running' })
        .where(and(eq(campaignSteps.id, stepId), inArray(campaignSteps.status, ['pending', 'failed'])))
        .returning({ id: campaignSteps.id, campaignId: campaignSteps.campaignId, retryCount: campaignSteps.retryCount });
      if (!claimed) continue;

      try {
        await markStepComplete(stepId, { note: 'step executed' });

        const [campaign] = await db.select().from(synthesisCampaigns).where(eq(synthesisCampaigns.id, claimed.campaignId));
        if (campaign) {
          const remaining = await db
            .select({ id: campaignSteps.id })
            .from(campaignSteps)
            .where(and(eq(campaignSteps.campaignId, claimed.campaignId), ne(campaignSteps.status, 'complete')));
          if (remaining.length === 0) {
            // Guard: only complete if not already in a terminal state (failed or complete).
            // .returning() gives an empty array when the WHERE predicate was false, so the
            // wiki enqueue only fires on an actual status transition.
            const updated = await db.update(synthesisCampaigns).set({ status: 'complete' }).where(
              and(eq(synthesisCampaigns.id, campaign.id), notInArray(synthesisCampaigns.status, [...TERMINAL_STATUSES])),
            ).returning({ id: synthesisCampaigns.id });
            if (updated.length > 0) {
              await boss.send('create-campaign-wiki', { campaignId: campaign.id }, { singletonKey: campaign.id }).catch(() => {});
            }
          }
        }
      } catch (err) {
        await markStepFailed(stepId, claimed.retryCount);
        console.error(`[campaign-worker] step ${stepId} failed:`, err);
      }
    }
  });

  await boss.work<{ campaignId: string }>('create-campaign-wiki', async (jobs) => {
    for (const job of jobs) {
      const { campaignId } = job.data;
      try {
        const [campaign] = await db.select().from(synthesisCampaigns).where(eq(synthesisCampaigns.id, campaignId));
        if (!campaign) continue;

        const plan = campaign.plan as Record<string, unknown> | null;
        const targetSmiles = campaign.targetSmiles ?? 'Unknown';
        const slug = `campaign-${campaignId.slice(0, 8)}`;
        const title = `Synthesis Campaign: ${targetSmiles}`;
        const contentText = [
          `Target: ${targetSmiles}`,
          `Status: ${campaign.status}`,
          plan ? `Plan: ${JSON.stringify(plan, null, 2)}` : '',
        ].filter(Boolean).join('\n\n');

        const wikiPageId = await upsertWikiPage(
          slug,
          title,
          { type: 'doc', content: [{ type: 'paragraph', content: [{ type: 'text', text: contentText }] }] },
          contentText,
          campaign.createdBy,
          [],
          embedTextsForWorker,
        );

        await db.update(synthesisCampaigns).set({ wikiPageId }).where(eq(synthesisCampaigns.id, campaignId));
        console.log(`[campaign-worker] wiki page created for campaign ${campaignId}: ${slug}`);
      } catch (err) {
        console.error(`[campaign-worker] wiki page creation failed for campaign ${campaignId}:`, err);
        throw err; // allow pg-boss to retry
      }
    }
  });
}
