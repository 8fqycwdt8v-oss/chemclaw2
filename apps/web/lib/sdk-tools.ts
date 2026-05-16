import { tool, createSdkMcpServer } from '@anthropic-ai/claude-agent-sdk';
import { z } from 'zod';
import {
  compoundSimilaritySearchTool,
  reactionSimilaritySearchTool,
  createWebSearchTool,
  createDocFetchTool,
  createElnFetchTool,
  createWikiFetchTool,
  createSynthesisCampaignTools,
  substructureCandidatesTool,
  interpretAnalyticalResultTool,
  createWikiUpsertTool,
  createWikiProposeTool,
  createRegisterPropertyTool,
  createRegisterPaperTool,
  hazardLookupTool,
  greenSolventTool,
  createContradictionTools,
  createDeepResearchTools,
  createLookupKnowledgeTool,
  lookupPropertiesTool,
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
    full: z.boolean().optional().describe('Return full content_text instead of a 2000-char preview (slug mode only)'),
  },
  async (args) => toMcpText(await wikiTool.execute(args)),
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

// Wave-2b B5: structured SAR rows for a specific compound. Stateless;
// no userId needed.
const lookupProperties = tool(
  lookupPropertiesTool.name,
  lookupPropertiesTool.description,
  {
    compound_id: z.string(),
    name: z.string().optional(),
    value_num_gte: z.number().optional(),
    value_num_lte: z.number().optional(),
    unit: z.string().optional(),
    limit: z.number().int().min(1).max(500).optional(),
  },
  async (args) => toMcpText(await lookupPropertiesTool.execute(args)),
);

// Wave-2b C3: unified retrieval across wiki + papers + properties + cached
// tool fetches (external_facts). Embedding is shared via the same embedText
// closure the wiki tool uses.
const lookupKnowledgeExec = createLookupKnowledgeTool(embedText);
const lookupKnowledge = tool(
  lookupKnowledgeExec.name,
  lookupKnowledgeExec.description,
  {
    query: z.string(),
    limit: z.number().int().min(1).max(50).optional(),
    types: z.array(z.enum(['wiki', 'paper', 'property', 'external'])).optional(),
    semantic: z.boolean().optional(),
  },
  async (args) => toMcpText(await lookupKnowledgeExec.execute(args)),
);

export function buildInProcessMcpServer(userId: string, sessionId?: string) {
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

  // Wave-3c opportunity #1: stage-for-review variant. Same shape as wiki_upsert
  // minus the project tag; result lands in wiki_proposed_edits.pending for an
  // admin to apply or reject.
  const wikiProposeToolExec = createWikiProposeTool(userId);
  const wikiPropose = tool(
    wikiProposeToolExec.name,
    wikiProposeToolExec.description,
    {
      slug: z.string(),
      title: z.string(),
      content_text: z.string(),
      rationale: z.string().optional(),
      citations: z.array(z.object({
        citationId: z.string(),
        sourceType: z.string(),
        sourceId: z.string().optional(),
        label: z.string(),
      })).optional(),
    },
    async (args) => toMcpText(await wikiProposeToolExec.execute(args)),
  );

  // Wave-3e B6: entity-extractor write tools. Sub-agent dispatch (defined in
  // SUBAGENT_DEFINITIONS) restricts entity-extractor to read tools + these
  // two; parent agent still has access for direct ingestion if needed.
  const registerPropertyExec = createRegisterPropertyTool(userId);
  const registerProperty = tool(
    registerPropertyExec.name,
    registerPropertyExec.description,
    {
      properties: z.array(z.object({
        compound_id: z.string(),
        name: z.string(),
        value_num: z.number().optional(),
        value_text: z.string().optional(),
        unit: z.string().optional(),
        method: z.string().optional(),
        source_citation_id: z.string().optional(),
        measured_at: z.string().optional(),
      })),
    },
    async (args) => toMcpText(await registerPropertyExec.execute(args)),
  );
  const registerPaperExec = createRegisterPaperTool(userId);
  const registerPaper = tool(
    registerPaperExec.name,
    registerPaperExec.description,
    {
      title: z.string(),
      doi: z.string().optional(),
      pubmed_id: z.string().optional(),
      url: z.string().optional(),
      abstract: z.string().optional(),
      content_text: z.string().optional(),
    },
    async (args) => toMcpText(await registerPaperExec.execute(args)),
  );

  const deepResearch = createDeepResearchTools(userId, embedTexts, sessionId);
  const beginDeepResearch = tool(
    deepResearch.begin.name,
    deepResearch.begin.description,
    { question: z.string() },
    async (args) => toMcpText(await deepResearch.begin.execute(args)),
  );
  const finalizeDeepResearch = tool(
    deepResearch.finalize.name,
    deepResearch.finalize.description,
    {
      slug: z.string(),
      title: z.string(),
      body: z.string(),
      citations: z.array(z.object({
        citationId: z.string(),
        sourceType: z.string(),
        sourceId: z.string().optional(),
        label: z.string(),
      })).optional(),
    },
    async (args) => toMcpText(await deepResearch.finalize.execute(args)),
  );

  // Wave-2a: persistence-bound tool factories. Each wraps the static export
  // and upserts results into external_facts so the next call (this session or
  // any other) can fast-path from world-state instead of re-fetching.
  const webSearchExec = createWebSearchTool(userId);
  const webSearch = tool(
    webSearchExec.name,
    webSearchExec.description,
    {
      query: z.string().describe('Search query'),
      site_filter: z.string().optional().describe('Restrict to an approved science domain'),
    },
    async (args) => toMcpText(await webSearchExec.execute(args)),
  );
  const docFetchExec = createDocFetchTool(userId);
  const docFetch = tool(
    docFetchExec.name,
    docFetchExec.description,
    {
      url: z.string().url().describe('URL from an approved science domain'),
      format: z.enum(['markdown', 'html', 'bytes']).optional().describe('Output format (default markdown)'),
    },
    async (args) => toMcpText(await docFetchExec.execute(args)),
  );
  const elnFetchExec = createElnFetchTool(userId);
  const elnFetch = tool(
    elnFetchExec.name,
    elnFetchExec.description,
    {
      experiment_id: z.string().describe('ELN experiment identifier (e.g. EXP-001)'),
    },
    async (args) => toMcpText(await elnFetchExec.execute(args)),
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
      wikiPropose,
      registerProperty,
      registerPaper,
      webSearch,
      docFetch,
      elnFetch,
      hazardLookup,
      greenSolvent,
      readTwoCitations,
      recordContradiction,
      beginDeepResearch,
      finalizeDeepResearch,
      substructureCandidates,
      interpretAnalyticalResult,
      lookupKnowledge,
      lookupProperties,
      startCampaign,
      confirmCampaign,
      kickoffCampaign,
    ],
  });
}
