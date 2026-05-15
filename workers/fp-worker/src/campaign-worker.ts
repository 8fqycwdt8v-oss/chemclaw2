import PgBoss from 'pg-boss';
import { ne, eq, and } from 'drizzle-orm';
import { db } from '@chemclaw2/db';
import { campaignSteps, synthesisCampaigns } from '@chemclaw2/db';
import {
  getStepsForRetry,
  markStepFailed,
  markStepComplete,
} from '@chemclaw2/db';

export async function startCampaignWorker(boss: PgBoss): Promise<void> {
  await boss.createQueue('retry-campaign-steps', { policy: PgBoss.policies.standard } as PgBoss.Queue);
  await boss.createQueue('run-campaign-step', { policy: PgBoss.policies.standard } as PgBoss.Queue);

  // Cron: every 5 minutes, find failed steps eligible for retry and re-enqueue them
  await boss.schedule('retry-campaign-steps', '*/5 * * * *');
  await boss.work('retry-campaign-steps', async () => {
    const stepsToRetry = await getStepsForRetry();
    for (const { id } of stepsToRetry) {
      await boss.send('run-campaign-step', { stepId: id }).catch(() => {});
    }
  });

  await boss.work<{ stepId: string }>('run-campaign-step', async (jobs) => {
    for (const job of jobs) {
      const { stepId } = job.data;
      const [step] = await db.select().from(campaignSteps).where(eq(campaignSteps.id, stepId));
      if (!step || step.status === 'complete') continue;

      try {
        // Mark in-progress
        await db.update(campaignSteps).set({ status: 'running' }).where(eq(campaignSteps.id, stepId));

        // Placeholder: actual step execution would call chemistry tools here
        // For now, mark complete with a stub result
        await markStepComplete(stepId, { note: 'step executed' });

        // Check if all steps for the campaign are complete
        const [campaign] = await db.select().from(synthesisCampaigns).where(eq(synthesisCampaigns.id, step.campaignId));
        if (campaign) {
          const remaining = await db
            .select({ id: campaignSteps.id })
            .from(campaignSteps)
            .where(and(eq(campaignSteps.campaignId, step.campaignId), ne(campaignSteps.status, 'complete')));
          if (remaining.length === 0) {
            await db.update(synthesisCampaigns).set({ status: 'complete' }).where(eq(synthesisCampaigns.id, campaign.id));
          }
        }
      } catch (err) {
        await markStepFailed(stepId, step.retryCount);
        console.error(`[campaign-worker] step ${stepId} failed:`, err);
      }
    }
  });
}
