import { createCampaign, updateCampaignStatusForUser, getCampaignBySession, addCampaignStep } from '@chemclaw2/db';

/**
 * Factory: captures userId from the authenticated request so the LLM cannot
 * supply an arbitrary created_by or campaign_id belonging to another user (IDOR prevention).
 */
export function createSynthesisCampaignTools(userId: string) {
  const synthesisCampaignTool = {
    name: 'start_synthesis_campaign',
    description:
      'Start a multi-step synthesis planning campaign for a target molecule. ' +
      'Creates a campaign record and returns the campaign ID. ' +
      'The agent should then call compound_similarity_search and find_similar_reactions to build the plan, ' +
      'then call confirm_synthesis_plan to save it.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        session_id: { type: 'string', description: 'Current session ID' },
        target_smiles: { type: 'string', description: 'Target molecule SMILES' },
      },
      required: ['session_id'],
    },
    async execute(input: { session_id: string; target_smiles?: string }) {
      const existing = await getCampaignBySession(input.session_id);
      if (existing) return { campaign_id: existing.id, status: existing.status };

      const id = await createCampaign(input.session_id, userId, input.target_smiles);
      return { campaign_id: id, status: 'planning' };
    },
  };

  const confirmSynthesisPlanTool = {
    name: 'confirm_synthesis_plan',
    description: 'Save the confirmed synthesis plan for a campaign and set status to awaiting_input.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        campaign_id: { type: 'string', description: 'Campaign ID from start_synthesis_campaign' },
        plan: {
          type: 'object',
          description: 'Synthesis plan with steps, conditions, and references',
        },
      },
      required: ['campaign_id', 'plan'],
    },
    async execute(input: { campaign_id: string; plan: Record<string, unknown> }) {
      const { found } = await updateCampaignStatusForUser(
        input.campaign_id,
        userId,
        'awaiting_input',
        input.plan,
      );
      if (!found) return { error: 'Campaign not found or access denied' };

      // Create individual step rows from the plan's steps array so the worker
      // can track and retry each step independently. Cap at 20 steps to prevent DoS.
      const MAX_STEPS = 20;
      const allSteps = Array.isArray(input.plan.steps) ? input.plan.steps as Array<Record<string, unknown>> : [];
      if (allSteps.length > MAX_STEPS) {
        return { error: `Plan exceeds maximum of ${MAX_STEPS} synthesis steps` };
      }
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

  return { synthesisCampaignTool, confirmSynthesisPlanTool };
}
