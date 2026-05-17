import {
  compoundSimilaritySearchTool,
  reactionSimilaritySearchTool,
  createWebSearchTool,
  createDocFetchTool,
  createElnFetchTool,
  createWikiFetchTool,
  createSynthesisCampaignTools,
  substructureCandidatesTool,
  createWikiUpsertTool,
  createWikiProposeTool,
  hazardLookupTool,
  greenSolventTool,
  createContradictionTools,
  createDeepResearchTools,
  createLookupKnowledgeTool,
  lookupPropertiesTool,
  type ToolDef,
  type ZodRawShape,
  type SubagentTag,
} from '@chemclaw2/agent-tools';
import { embedText, embedTexts } from './embeddings';

/**
 * Single registry. Adding a new tool happens here and only here:
 *   - in-process tool factories return ToolDefs that drive registration AND
 *     sub-agent allow-listing via the `subagents` tag on each ToolDef.
 *   - external stdio MCP tools (mcp-molfp / mcp-rxnfp) are declared with
 *     fully-qualified names alongside the sub-agents they're exposed to.
 *   - `experimentTools` lists in-process tool names that count against the
 *     `experiments_cap` budget (everything else hits `tool_calls_cap` only).
 *
 * Both apps/web/lib/sdk-tools.ts (transport) and apps/web/lib/agent.ts
 * (budget classification + sub-agent tool list) read from here. No silent
 * "forgot to register" failure mode: a tool that's not in this registry
 * isn't built into the in-process MCP server.
 */

/** Tool defs whose factory takes (userId, sessionId?, ...) at request scope. */
export function buildInProcessToolDefs(
  userId: string,
  sessionId?: string,
): Array<ToolDef<ZodRawShape>> {
  const campaign = createSynthesisCampaignTools(userId);
  const deepResearch = createDeepResearchTools(userId, embedTexts, sessionId);
  const contradiction = createContradictionTools(userId);
  // Cast each entry to `ToolDef<ZodRawShape>` so the heterogeneous tuple
  // collapses into an array the SDK can iterate. The inner schemas are still
  // type-safe at the factory definition site.
  return [
    compoundSimilaritySearchTool,
    reactionSimilaritySearchTool,
    createWikiFetchTool(embedText),
    hazardLookupTool,
    greenSolventTool,
    substructureCandidatesTool,
    lookupPropertiesTool,
    createLookupKnowledgeTool(embedText),
    createWebSearchTool(userId),
    createDocFetchTool(userId),
    createElnFetchTool(userId),
    createWikiUpsertTool(userId, embedTexts),
    createWikiProposeTool(userId),
    contradiction.readTwo,
    contradiction.record,
    deepResearch.finalize,
    campaign.synthesisCampaignTool,
    campaign.confirmSynthesisPlanTool,
    campaign.kickoffCampaignTool,
  ] as Array<ToolDef<ZodRawShape>>;
}

/**
 * External stdio MCP tools live outside the ToolDef contract (they're hosted
 * by the Python servers in packages/mcp-servers). Whitelist them per sub-agent
 * so the deep-research path keeps the fingerprint compute tools it relied on.
 */
export const externalMcpToolsBySubagent: Record<SubagentTag, readonly string[]> = {
  'deep-research': [
    'mcp__mcp-molfp__compute_morgan_fp',
    'mcp__mcp-molfp__substructure_match',
    'mcp__mcp-rxnfp__compute_drfp',
  ],
  'contradiction-resolver': [],
};

/**
 * In-process tool names that count against the `experiments_cap` budget.
 * Everything else only hits `tool_calls_cap`. Lives alongside the tool list
 * so adding a new experiment-tier tool is one diff, not two.
 */
export const experimentToolNames: ReadonlySet<string> = new Set([
  'kickoff_campaign',
]);
