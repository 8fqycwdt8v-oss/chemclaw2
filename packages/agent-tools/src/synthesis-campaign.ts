import { createCampaign, updateCampaignStatusForUser, getCampaignBySession, addCampaignStep, db, sql } from '@chemclaw2/db';

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
      // Validate step count before writing to DB — avoids leaving campaign stuck in awaiting_input
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

      // Create individual step rows from the plan's steps array so the worker
      // can track and retry each step independently.
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

  const kickoffCampaignTool = {
    name: 'kickoff_campaign',
    description:
      'After the user has reviewed and approved the synthesis plan, flip the campaign from ' +
      'awaiting_input to running so the worker begins executing steps. Ask the user for ' +
      'explicit confirmation BEFORE calling this tool (it kicks off real (or simulated) ' +
      'experiment dispatch). Idempotent — re-calling on a running campaign is a no-op.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        campaign_id: { type: 'string', description: 'Campaign ID' },
      },
      required: ['campaign_id'],
    },
    async execute(input: { campaign_id: string }) {
      const { found } = await updateCampaignStatusForUser(input.campaign_id, userId, 'running');
      if (!found) return { error: 'Campaign not found, not owned by you, or already terminal' };
      // Reset next_retry_at so the worker poll picks pending steps up immediately.
      // The schema default for status is 'pending' (see migration 0004), so newly
      // inserted steps from confirm_synthesis_plan are already in the right state.
      await db.execute(
        sql`UPDATE campaign_steps SET next_retry_at = NOW() WHERE campaign_id = ${input.campaign_id}::uuid AND status = 'pending'`,
      );
      return { status: 'running', message: 'Campaign kicked off — worker will execute steps.' };
    },
  };

  return { synthesisCampaignTool, confirmSynthesisPlanTool, kickoffCampaignTool };
}
