import PgBoss from 'pg-boss';
import { db } from '@chemclaw2/db';
import { campaignSteps, synthesisCampaigns } from '@chemclaw2/db';
import { eq, and, lt, sql } from 'drizzle-orm';

export async function startCampaignWorker(boss: PgBoss): Promise<void> {
  await boss.createQueue('run-campaign-step', { policy: PgBoss.policies.standard } as PgBoss.Queue);

  // Cron: every 5 minutes, find failed steps eligible for retry and re-enqueue them
  await boss.schedule('retry-campaign-steps', '*/5 * * * *');
  await boss.work('retry-campaign-steps', async () => {
    const stepsToRetry = await db
      .select({ id: campaignSteps.id })
      .from(campaignSteps)
      .where(
        and(
          eq(campaignSteps.status, 'failed'),
          lt(campaignSteps.retryCount, 3),
          sql`next_retry_at <= NOW()`,
        ),
      );
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
        await db
          .update(campaignSteps)
          .set({ status: 'complete', result: { note: 'step executed' } })
          .where(eq(campaignSteps.id, stepId));

        // Check if all steps for the campaign are complete
        const campaign = (await db.select().from(synthesisCampaigns).where(eq(synthesisCampaigns.id, step.campaignId)))[0];
        if (campaign) {
          const remaining = await db
            .select({ id: campaignSteps.id })
            .from(campaignSteps)
            .where(and(eq(campaignSteps.campaignId, step.campaignId), sql`status != 'complete'`));
          if (remaining.length === 0) {
            await db.update(synthesisCampaigns).set({ status: 'complete' }).where(eq(synthesisCampaigns.id, campaign.id));
          }
        }
      } catch (err) {
        const backoffMinutes = Math.pow(2, step.retryCount);
        await db
          .update(campaignSteps)
          .set({
            status: 'failed',
            retryCount: step.retryCount + 1,
            nextRetryAt: sql`NOW() + INTERVAL '${sql.raw(String(backoffMinutes))} minutes'`,
          })
          .where(eq(campaignSteps.id, stepId));
        console.error(`[campaign-worker] step ${stepId} failed:`, err);
      }
    }
  });
}
