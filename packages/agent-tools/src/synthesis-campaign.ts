import { createCampaign, updateCampaignStatus, getCampaignBySession } from '@chemclaw2/db';

export const synthesisCampaignTool = {
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
      created_by: { type: 'string', description: 'User ID' },
    },
    required: ['session_id', 'created_by'],
  },
  async execute(input: { session_id: string; target_smiles?: string; created_by: string }) {
    // Return existing campaign if one already exists for this session
    const existing = await getCampaignBySession(input.session_id);
    if (existing) return { campaign_id: existing.id, status: existing.status };

    const id = await createCampaign(input.session_id, input.created_by, input.target_smiles);
    return { campaign_id: id, status: 'planning' };
  },
};

export const confirmSynthesisPlanTool = {
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
    await updateCampaignStatus(input.campaign_id, 'awaiting_input', input.plan);
    return { status: 'awaiting_input', message: 'Plan saved. Waiting for user confirmation.' };
  },
};
