import { z } from 'zod';
import {
  createCampaign,
  updateCampaignStatusForUser,
  getCampaignBySession,
  getCampaignWithStepsForUser,
  addCampaignStep,
  replaceSessionTodos,
  startPendingStepsForUser,
} from '@chemclaw2/db';
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
      const MAX_STEPS = 20;
      const allSteps = Array.isArray(input.plan.steps) ? input.plan.steps as Array<Record<string, unknown>> : [];
      if (allSteps.length > MAX_STEPS) {
        return { error: `Plan exceeds maximum of ${MAX_STEPS} synthesis steps` };
      }

      const { found } = await updateCampaignStatusForUser(
        input.campaign_id,
        userId,
        'awaiting_input',
        input.plan,
      );
      if (!found) return { error: 'Campaign not found or access denied' };

      for (let i = 0; i < allSteps.length; i++) {
        const s = allSteps[i];
        await addCampaignStep(input.campaign_id, i, {
          reactionSmiles: typeof s.reaction_smiles === 'string' ? s.reaction_smiles : undefined,
          conditions: typeof s.conditions === 'string' ? s.conditions : undefined,
        });
      }

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
      // Owner-scoped step updates run via the db layer rather than inline SQL
      // so the campaign queries module owns the predicate.
      await startPendingStepsForUser(input.campaign_id, userId, {
        perStepApproval: input.approval === 'per_step',
      });

      const owned = await getCampaignWithStepsForUser(input.campaign_id, userId).catch(() => null);
      if (owned) {
        const items = owned.steps.map((s, i) => {
          const desc = s.reactionSmiles ?? s.conditions ?? '(step body pending)';
          return `Campaign step ${i}: ${desc.slice(0, 200)}`;
        });
        if (items.length > 0) {
          await replaceSessionTodos(owned.campaign.sessionId, userId, items).catch((err) => {
            console.error('[kickoff_campaign] replaceSessionTodos failed:', err);
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
