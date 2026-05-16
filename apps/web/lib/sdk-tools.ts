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
  interpretAnalyticalResultTool,
  createWikiUpsertTool,
  hazardLookupTool,
  greenSolventTool,
  createContradictionTools,
} from '@chemclaw2/agent-tools';
import { embedText, embedTexts } from './embeddings';

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
    format: z.enum(['markdown', 'html', 'bytes']).optional().describe('Output format (default markdown)'),
  },
  async (args) => toMcpText(await docFetchTool.execute(args)),
);

const hazardLookup = tool(
  hazardLookupTool.name,
  hazardLookupTool.description,
  {
    cas_or_smiles: z.string(),
    kind: z.enum(['cas', 'smiles']),
  },
  async (args) => toMcpText(await hazardLookupTool.execute(args)),
);

const greenSolvent = tool(
  greenSolventTool.name,
  greenSolventTool.description,
  {
    solvents: z.array(z.string()).describe('SMILES of solvents to score'),
  },
  async (args) => toMcpText(await greenSolventTool.execute(args)),
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

const interpretAnalyticalResult = tool(
  interpretAnalyticalResultTool.name,
  interpretAnalyticalResultTool.description,
  {
    technique: z.enum(['NMR', 'MS', 'IR']),
    observations: z.string().describe('Observed peaks / fragments / signals (free text)'),
    proposed_structure_smiles: z.string().optional(),
    proposed_fingerprint_bits: z.string().optional(),
  },
  async (args) => toMcpText(await interpretAnalyticalResultTool.execute(args)),
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
    {
      campaign_id: z.string(),
      approval: z.enum(['per_step', 'all_at_once']).optional()
        .describe('per_step: only step 0 runs automatically, the rest wait for /approve. Default: all_at_once.'),
    },
    async (args) => toMcpText(await campaign.kickoffCampaignTool.execute(args)),
  );
  const wikiUpsertTool = createWikiUpsertTool(userId, embedTexts);
  const wikiUpsert = tool(
    wikiUpsertTool.name,
    wikiUpsertTool.description,
    {
      slug: z.string(),
      title: z.string(),
      content_text: z.string(),
      project: z.string().optional(),
      citations: z.array(z.object({
        citationId: z.string(),
        sourceType: z.string(),
        sourceId: z.string().optional(),
        label: z.string(),
      })).optional(),
    },
    async (args) => toMcpText(await wikiUpsertTool.execute(args)),
  );

  const contradiction = createContradictionTools(userId);
  const readTwoCitations = tool(
    contradiction.readTwo.name,
    contradiction.readTwo.description,
    {
      slug: z.string(),
      citation_a: z.string(),
      citation_b: z.string(),
    },
    async (args) => toMcpText(await contradiction.readTwo.execute(args)),
  );
  const recordContradiction = tool(
    contradiction.record.name,
    contradiction.record.description,
    {
      slug: z.string(),
      citation_a: z.string(),
      citation_b: z.string(),
      winner: z.enum(['a', 'b', 'inconclusive']),
      reason: z.string(),
    },
    async (args) => toMcpText(await contradiction.record.execute(args)),
  );

  return createSdkMcpServer({
    name: 'chemclaw2-tools',
    tools: [
      compoundSearch,
      reactionSearch,
      wikiLookup,
      wikiUpsert,
      webSearch,
      docFetch,
      elnFetch,
      hazardLookup,
      greenSolvent,
      readTwoCitations,
      recordContradiction,
      substructureCandidates,
      interpretAnalyticalResult,
      startCampaign,
      confirmCampaign,
      kickoffCampaign,
    ],
  });
}
