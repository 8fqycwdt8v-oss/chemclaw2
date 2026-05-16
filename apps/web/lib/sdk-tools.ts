import { tool, createSdkMcpServer } from '@anthropic-ai/claude-agent-sdk';
import { z } from 'zod';
import {
  compoundSimilaritySearchTool,
  reactionSimilaritySearchTool,
  webSearchTool,
  docFetchTool,
  elnFetchTool,
  createWikiFetchTool,
  createSynthesisCampaignTools,
  substructureCandidatesTool,
} from '@chemclaw2/agent-tools';
import { embedText } from './embeddings';

function toMcpText(result: unknown) {
  return { content: [{ type: 'text' as const, text: JSON.stringify(result) }] };
}

const compoundSearch = tool(
  compoundSimilaritySearchTool.name,
  compoundSimilaritySearchTool.description,
  {
    fingerprint_bits: z.string().describe('Morgan fingerprint as 2048-char binary string from compute_morgan_fp'),
    min_tanimoto: z.number().min(0).max(1).optional().describe('Minimum Tanimoto score (0–1)'),
    limit: z.number().int().min(1).max(50).optional().describe('Max results to return'),
  },
  async (args) => toMcpText(await compoundSimilaritySearchTool.execute(args)),
);

const reactionSearch = tool(
  reactionSimilaritySearchTool.name,
  reactionSimilaritySearchTool.description,
  {
    fingerprint_bits: z.string().describe('DRFP fingerprint as 2048-char binary string from compute_drfp'),
    min_similarity: z.number().min(0).max(1).optional().describe('Minimum similarity score (0–1)'),
    limit: z.number().int().min(1).max(50).optional().describe('Max results to return'),
  },
  async (args) => toMcpText(await reactionSimilaritySearchTool.execute(args)),
);

const wikiTool = createWikiFetchTool(embedText);
const wikiLookup = tool(
  wikiTool.name,
  wikiTool.description,
  {
    slug: z.string().optional().describe('Direct page slug (e.g. "aspirin")'),
    query: z.string().optional().describe('Full-text or semantic search query'),
    semantic: z.boolean().optional().describe('Use vector similarity search (requires query)'),
  },
  async (args) => toMcpText(await wikiTool.execute(args)),
);

const webSearch = tool(
  webSearchTool.name,
  webSearchTool.description,
  {
    query: z.string().describe('Search query'),
    site_filter: z.string().optional().describe('Restrict to an approved science domain'),
  },
  async (args) => toMcpText(await webSearchTool.execute(args)),
);

const docFetch = tool(
  docFetchTool.name,
  docFetchTool.description,
  {
    url: z.string().url().describe('URL from an approved science domain'),
  },
  async (args) => toMcpText(await docFetchTool.execute(args)),
);

const elnFetch = tool(
  elnFetchTool.name,
  elnFetchTool.description,
  {
    experiment_id: z.string().describe('ELN experiment identifier (e.g. EXP-001)'),
  },
  async (args) => toMcpText(await elnFetchTool.execute(args)),
);

const substructureCandidates = tool(
  substructureCandidatesTool.name,
  substructureCandidatesTool.description,
  {
    max_candidates: z.number().int().min(1).max(5000).optional(),
  },
  async (args) => toMcpText(await substructureCandidatesTool.execute(args)),
);

export function buildInProcessMcpServer(userId: string) {
  const campaign = createSynthesisCampaignTools(userId);
  const startCampaign = tool(
    campaign.synthesisCampaignTool.name,
    campaign.synthesisCampaignTool.description,
    {
      session_id: z.string().describe('Current session ID'),
      target_smiles: z.string().optional().describe('Target molecule SMILES'),
    },
    async (args) => toMcpText(await campaign.synthesisCampaignTool.execute(args)),
  );
  const confirmCampaign = tool(
    campaign.confirmSynthesisPlanTool.name,
    campaign.confirmSynthesisPlanTool.description,
    {
      campaign_id: z.string(),
      plan: z.record(z.string(), z.unknown()),
    },
    async (args) => toMcpText(await campaign.confirmSynthesisPlanTool.execute(args)),
  );
  const kickoffCampaign = tool(
    campaign.kickoffCampaignTool.name,
    campaign.kickoffCampaignTool.description,
    { campaign_id: z.string() },
    async (args) => toMcpText(await campaign.kickoffCampaignTool.execute(args)),
  );

  return createSdkMcpServer({
    name: 'chemclaw2-tools',
    tools: [
      compoundSearch,
      reactionSearch,
      wikiLookup,
      webSearch,
      docFetch,
      elnFetch,
      substructureCandidates,
      startCampaign,
      confirmCampaign,
      kickoffCampaign,
    ],
  });
}
