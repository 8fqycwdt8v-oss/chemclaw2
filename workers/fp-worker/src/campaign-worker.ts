import PgBoss from 'pg-boss';
import OpenAI from 'openai';
import { ne, eq, and, lt, inArray, notInArray, sql } from 'drizzle-orm';
import { db } from '@chemclaw2/db';
import { campaignSteps, synthesisCampaigns, reactions } from '@chemclaw2/db';
import { upsertWikiPage, findSimilarReactions } from '@chemclaw2/db';
import {
  getStepsForRetry,
  markStepFailed,
  markStepComplete,
  TERMINAL_STATUSES,
} from '@chemclaw2/db';
import {
  EMBED_MODEL,
  EMBED_DIM,
  prepareEmbeddingInputs,
  stripMarkdownForEmbedding,
} from '@chemclaw2/agent-tools';

let openaiClient: OpenAI | undefined;
function getOpenAI(): OpenAI {
  if (!openaiClient) openaiClient = new OpenAI({ apiKey: process.env.OPENAI_API_KEY! });
  return openaiClient;
}

async function embedTextsForWorker(texts: string[]): Promise<number[][]> {
  if (texts.length === 0) return [];
  const stripped = texts.map(stripMarkdownForEmbedding);
  const inputs = prepareEmbeddingInputs(stripped);
  const res = await getOpenAI().embeddings.create({ model: EMBED_MODEL, input: inputs });
  return res.data.map((d) => {
    if (d.embedding.length !== EMBED_DIM) {
      throw new Error(`embedTextsForWorker: vector dim ${d.embedding.length} ≠ expected ${EMBED_DIM}`);
    }
    return d.embedding;
  });
}

// Steps stuck in 'running' past this are assumed crashed and reset by the sweep.
const DEAD_LETTER_TIMEOUT_MINUTES = 30;

async function buildStepResult(reactionSmiles: string | null): Promise<Record<string, unknown>> {
  const executedAt = new Date().toISOString();
  if (!reactionSmiles) return { executedAt, note: 'no reaction_smiles — skipped enrichment' };
  const [match] = await db
    .select({ id: reactions.id, drfp: reactions.drfp })
    .from(reactions)
    .where(eq(reactions.rxnSmiles, reactionSmiles))
    .limit(1);
  if (!match?.drfp) {
    return { executedAt, reactionSmiles, note: 'reaction not in registry or not yet fingerprinted' };
  }
  const neighbors = await findSimilarReactions(match.drfp, 5, 0.4);
  return {
    executedAt,
    reactionSmiles,
    matchedReactionId: match.id,
    neighbors: neighbors.map((n) => ({
      id: n.id,
      rxnSmiles: n.rxnSmiles,
      name: n.name,
      conditions: n.conditions,
      similarity: n.similarity,
    })),
  };
}

export async function startCampaignWorker(boss: PgBoss): Promise<void> {
  if (!process.env.OPENAI_API_KEY) {
    throw new Error('OPENAI_API_KEY is required for campaign wiki creation');
  }
  await boss.createQueue('retry-campaign-steps', { policy: PgBoss.policies.standard } as PgBoss.Queue);
  await boss.createQueue('run-campaign-step', { policy: PgBoss.policies.stately } as PgBoss.Queue);
  await boss.createQueue('create-campaign-wiki', { policy: PgBoss.policies.stately } as PgBoss.Queue);

  await boss.schedule('retry-campaign-steps', '*/5 * * * *');
  await boss.work('retry-campaign-steps', async () => {
    // Pending steps in a kicked-off campaign — getStepsForRetry only covers failed,
    // so without this the steps confirm_synthesis_plan inserted would sit forever.
    const pending = await db
      .select({ id: campaignSteps.id })
      .from(campaignSteps)
      .innerJoin(synthesisCampaigns, eq(synthesisCampaigns.id, campaignSteps.campaignId))
      .where(and(
        eq(campaignSteps.status, 'pending'),
        eq(campaignSteps.requiresApproval, false),
        eq(synthesisCampaigns.status, 'running'),
      ))
      .limit(50);
    for (const { id } of pending) {
      await boss.send('run-campaign-step', { stepId: id }, { singletonKey: id }).catch(() => {});
    }

    // Dead-letter sweep: reset stuck 'running' steps so getStepsForRetry picks them up.
    // Don't increment retry_count here — markStepFailed owns the retry counter so
    // we don't double-count a sweep-then-real-failure as two attempts.
    await db
      .update(campaignSteps)
      .set({ status: 'failed', nextRetryAt: sql`NOW()` })
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
        .where(and(
          eq(campaignSteps.id, stepId),
          inArray(campaignSteps.status, ['pending', 'failed']),
          eq(campaignSteps.requiresApproval, false),
        ))
        .returning({
          id: campaignSteps.id,
          campaignId: campaignSteps.campaignId,
          retryCount: campaignSteps.retryCount,
          reactionSmiles: campaignSteps.reactionSmiles,
        });
      if (!claimed) continue;

      try {
        const result = await buildStepResult(claimed.reactionSmiles);
        await markStepComplete(stepId, result);

        const [campaign] = await db.select().from(synthesisCampaigns).where(eq(synthesisCampaigns.id, claimed.campaignId));
        if (campaign) {
          const remaining = await db
            .select({ id: campaignSteps.id })
            .from(campaignSteps)
            .where(and(eq(campaignSteps.campaignId, claimed.campaignId), ne(campaignSteps.status, 'complete')));
          if (remaining.length === 0) {
            // Guard against re-entering terminal state — only enqueue wiki on real transition.
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
        // M8: full campaign UUID in the slug — 8-char prefix collides at ~10^-5
        // birthday probability per 1000 campaigns.
        const slug = `campaign-${campaignId}`;
        const title = `Synthesis Campaign: ${targetSmiles}`;

        // M7: pull executed step results and emit citations for any matched
        // reaction neighbors. The result payload (markStepComplete) carries the
        // neighbor reaction ids; we cite those.
        const steps = await db
          .select({ stepIdx: campaignSteps.stepIdx, result: campaignSteps.result })
          .from(campaignSteps)
          .where(eq(campaignSteps.campaignId, campaignId))
          .orderBy(campaignSteps.stepIdx);

        const citations: Array<{ citationId: string; sourceType: string; sourceId?: string; label: string }> = [];
        const seenReactionIds = new Set<string>();
        let citationCounter = 1;
        const stepLines: string[] = [];
        for (const s of steps) {
          const result = (s.result ?? {}) as Record<string, unknown>;
          const neighbors = Array.isArray(result.neighbors) ? result.neighbors as Array<Record<string, unknown>> : [];
          const refs: string[] = [];
          for (const n of neighbors) {
            const id = typeof n.id === 'string' ? n.id : null;
            if (!id || seenReactionIds.has(id)) continue;
            seenReactionIds.add(id);
            const cid = String(citationCounter++);
            citations.push({
              citationId: cid,
              sourceType: 'reaction',
              sourceId: id,
              label: typeof n.name === 'string' ? n.name : `reaction ${id.slice(0, 8)}`,
            });
            refs.push(`[${cid}]`);
          }
          const rxn = typeof result.reactionSmiles === 'string' ? result.reactionSmiles : null;
          stepLines.push(`Step ${s.stepIdx}: ${rxn ?? '(no reaction)'}${refs.length ? ` ${refs.join(' ')}` : ''}`);
        }

        const contentText = [
          `Target: ${targetSmiles}`,
          `Status: ${campaign.status}`,
          stepLines.length ? `Steps:\n${stepLines.join('\n')}` : '',
          plan ? `Plan: ${JSON.stringify(plan, null, 2)}` : '',
        ].filter(Boolean).join('\n\n');

        const wikiPageId = await upsertWikiPage(
          slug,
          title,
          { type: 'doc', content: [{ type: 'paragraph', content: [{ type: 'text', text: contentText }] }] },
          contentText,
          campaign.createdBy,
          citations,
          embedTextsForWorker,
          { needsReview: true },
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
