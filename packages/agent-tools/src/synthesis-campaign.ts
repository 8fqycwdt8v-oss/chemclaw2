import { z } from 'zod';
import {
  createCampaign,
  updateCampaignStatusForUser,
  getCampaignBySession,
  getCampaignWithStepsForUser,
  confirmCampaignPlanForUser,
  replaceSessionTodos,
  db,
  sql,
} from '@chemclaw2/db';
import { logger } from '@chemclaw2/observability';
import { UUID_RE } from './uuid';
import type { ToolDef } from './tool-def';

const startSchema = {
  session_id: z.string().describe('Current session ID'),
  target_smiles: z.string().optional().describe('Target molecule SMILES'),
};
const confirmSchema = {
  campaign_id: z.string().describe('Campaign ID from start_synthesis_campaign'),
  plan: z.record(z.string(), z.unknown()).describe(
    'Synthesis plan with steps, conditions, and references',
  ),
};
const kickoffSchema = {
  campaign_id: z.string().describe('Campaign ID'),
  approval: z.enum(['per_step', 'all_at_once']).optional().describe(
    'per_step gates each non-first step on user approval. Default all_at_once.',
  ),
};

/**
 * Factory: captures userId from the authenticated request so the LLM cannot
 * supply an arbitrary created_by or campaign_id belonging to another user (IDOR prevention).
 */
export function createSynthesisCampaignTools(userId: string): {
  synthesisCampaignTool: ToolDef<typeof startSchema>;
  confirmSynthesisPlanTool: ToolDef<typeof confirmSchema>;
  kickoffCampaignTool: ToolDef<typeof kickoffSchema>;
} {
  const synthesisCampaignTool: ToolDef<typeof startSchema> = {
    name: 'start_synthesis_campaign',
    description:
      'Start a multi-step synthesis planning campaign for a target molecule. ' +
      'Creates a campaign record and returns the campaign ID. ' +
      'The agent should then call compound_similarity_search and find_similar_reactions to build the plan, ' +
      'then call confirm_synthesis_plan to save it.',
    schema: startSchema,
    async execute(input) {
      if (!UUID_RE.test(input.session_id)) {
        return { error: 'session_id must be a UUID' };
      }
      const existing = await getCampaignBySession(input.session_id, userId);
      if (existing) return { campaign_id: existing.id, status: existing.status };

      const id = await createCampaign(input.session_id, userId, input.target_smiles);
      return { campaign_id: id, status: 'planning' };
    },
  };

  const confirmSynthesisPlanTool: ToolDef<typeof confirmSchema> = {
    name: 'confirm_synthesis_plan',
    description: 'Save the confirmed synthesis plan for a campaign and set status to awaiting_input.',
    schema: confirmSchema,
    async execute(input) {
      if (!UUID_RE.test(input.campaign_id)) {
        return { error: 'campaign_id must be a UUID' };
      }
      // Validate step count before writing to DB — avoids leaving campaign stuck in awaiting_input
      const MAX_STEPS = 20;
      const allSteps = Array.isArray(input.plan.steps) ? input.plan.steps as Array<Record<string, unknown>> : [];
      if (allSteps.length > MAX_STEPS) {
        return { error: `Plan exceeds maximum of ${MAX_STEPS} synthesis steps` };
      }

      // Status flip + step inserts run in one transaction so a mid-loop
      // insert failure can't leave the campaign wedged in awaiting_input
      // with a partial step list.
      const stepRows = allSteps.map((s) => ({
        reactionSmiles: typeof s.reaction_smiles === 'string' ? s.reaction_smiles : undefined,
        conditions: typeof s.conditions === 'string' ? s.conditions : undefined,
      }));
      const { found } = await confirmCampaignPlanForUser(
        input.campaign_id,
        userId,
        input.plan,
        stepRows,
      );
      if (!found) return { error: 'Campaign not found or access denied' };

      return { status: 'awaiting_input', message: 'Plan saved. Waiting for user confirmation.', steps_created: allSteps.length };
    },
  };

  const kickoffCampaignTool: ToolDef<typeof kickoffSchema> = {
    name: 'kickoff_campaign',
    description:
      'After the user has reviewed and approved the synthesis plan, flip the campaign from ' +
      'awaiting_input to running so the worker begins executing steps. Ask the user for ' +
      'explicit confirmation BEFORE calling this tool (it kicks off real (or simulated) ' +
      'experiment dispatch). Idempotent — re-calling on a running campaign is a no-op. ' +
      'approval=per_step: only step 0 runs automatically; subsequent steps wait for ' +
      'POST /api/campaigns/[id]/steps/[idx]/approve.',
    schema: kickoffSchema,
    async execute(input) {
      if (!UUID_RE.test(input.campaign_id)) {
        return { error: 'campaign_id must be a UUID' };
      }
      const { found } = await updateCampaignStatusForUser(input.campaign_id, userId, 'running');
      if (!found) return { error: 'Campaign not found, not owned by you, or already terminal' };
      // Re-check ownership in the raw SQL too — defence-in-depth so a race
      // window between the status update and these UPDATEs can't be used to
      // mutate steps on someone else's campaign.
      await db.execute(
        sql`UPDATE campaign_steps SET next_retry_at = NOW()
            WHERE campaign_id = ${input.campaign_id}::uuid
              AND status = 'pending'
              AND campaign_id IN (SELECT id FROM synthesis_campaigns WHERE created_by = ${userId})`,
      );
      if (input.approval === 'per_step') {
        await db.execute(
          sql`UPDATE campaign_steps SET requires_approval = true
              WHERE campaign_id = ${input.campaign_id}::uuid
                AND step_idx > 0
                AND campaign_id IN (SELECT id FROM synthesis_campaigns WHERE created_by = ${userId})`,
        );
      }

      // BACKLOG #45: seed agent_todos so the campaign's step list surfaces in
      // the chat UI todo panel alongside deep-research checklists. One todo
      // per step, ordered by stepIdx; text mirrors the campaign_step row
      // (reactionSmiles, then conditions, then a placeholder for empty steps).
      // Best-effort: a persistence failure here must not block the kickoff
      // itself, mirroring deep-research's handling.
      const owned = await getCampaignWithStepsForUser(input.campaign_id, userId).catch((err) => {
        logger.error('get_campaign_with_steps_failed', { campaign_id: input.campaign_id, user_id: userId }, err);
        return null;
      });
      if (owned) {
        const items = owned.steps.map((s, i) => {
          const desc = s.reactionSmiles ?? s.conditions ?? '(step body pending)';
          return `Campaign step ${i}: ${desc.slice(0, 200)}`;
        });
        if (items.length > 0) {
          await replaceSessionTodos(owned.campaign.sessionId, userId, items).catch((err) => {
            logger.error('replace_session_todos_failed', {
              session_id: owned.campaign.sessionId,
              user_id: userId,
              item_count: items.length,
            }, err);
          });
        }
      }

      if (input.approval === 'per_step') {
        return {
          status: 'running',
          approval_mode: 'per_step',
          message: 'Step 0 will execute; subsequent steps await /approve calls.',
        };
      }
      return { status: 'running', approval_mode: 'all_at_once', message: 'Worker will execute all steps.' };
    },
  };

  return { synthesisCampaignTool, confirmSynthesisPlanTool, kickoffCampaignTool };
}
