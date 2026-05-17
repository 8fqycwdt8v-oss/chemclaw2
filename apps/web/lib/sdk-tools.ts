import { tool, createSdkMcpServer } from '@anthropic-ai/claude-agent-sdk';
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

const MCP_SERVER_NAME = 'chemclaw2-tools';
const mcpName = (toolName: string) => `mcp__${MCP_SERVER_NAME}__${toolName}`;

/** External MCP tools (mcp-molfp / mcp-rxnfp) live outside ToolDef tagging.
 * Whitelist them here per sub-agent so the deep-research path keeps the
 * fingerprint compute tools it relied on pre-refactor. */
const EXTERNAL_MCP_TOOLS_BY_SUBAGENT: Record<SubagentTag, string[]> = {
  'deep-research': [
    'mcp__mcp-molfp__compute_morgan_fp',
    'mcp__mcp-molfp__substructure_match',
    'mcp__mcp-rxnfp__compute_drfp',
  ],
  'contradiction-resolver': [],
};

function toMcpText(result: unknown) {
  return { content: [{ type: 'text' as const, text: JSON.stringify(result) }] };
}

/**
 * Wave-3g A6: one helper instead of per-tool boilerplate. Every tool factory
 * exports `{ name, description, schema, execute }` via the `ToolDef<S>`
 * contract; `registerTool` adapts that into the SDK's `tool(...)` shape and
 * wraps the response in MCP text. The agent-tools package owns the schema;
 * sdk-tools.ts owns transport.
 */
function registerTool<S extends ZodRawShape>(t: ToolDef<S>) {
  // SDK's InferShape and our z.infer<z.ZodObject<S>> diverge at the type
  // level (two inference paths through the same Zod module) but produce
  // identical runtime shapes. Cast the handler arg to bridge.
  return tool(
    t.name,
    t.description,
    t.schema,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    async (args) => toMcpText(await t.execute(args as any)),
  );
}

/** Minimal projection of a ToolDef used for tag-based name derivation. The
 * full ToolDef<S> generic doesn't fit into a uniform array because the input
 * type is contravariant; this projection keeps only what the subagent path
 * needs (name + tags). */
type TaggedTool = { name: string; subagents?: readonly SubagentTag[] };

function allToolDefs(userId: string, sessionId?: string) {
  const campaign = createSynthesisCampaignTools(userId);
  const deepResearch = createDeepResearchTools(userId, embedTexts, sessionId);
  const contradiction = createContradictionTools(userId);
  return {
    compoundSimilaritySearchTool,
    reactionSimilaritySearchTool,
    wikiFetch: createWikiFetchTool(embedText),
    hazardLookupTool,
    greenSolventTool,
    substructureCandidatesTool,
    lookupPropertiesTool,
    lookupKnowledge: createLookupKnowledgeTool(embedText),
    webSearch: createWebSearchTool(userId),
    docFetch: createDocFetchTool(userId),
    elnFetch: createElnFetchTool(userId),
    wikiUpsert: createWikiUpsertTool(userId, embedTexts),
    wikiPropose: createWikiProposeTool(userId),
    contradictionReadTwo: contradiction.readTwo,
    contradictionRecord: contradiction.record,
    deepResearchFinalize: deepResearch.finalize,
    campaignStart: campaign.synthesisCampaignTool,
    campaignConfirm: campaign.confirmSynthesisPlanTool,
    campaignKickoff: campaign.kickoffCampaignTool,
  };
}

export function buildInProcessMcpServer(userId: string, sessionId?: string) {
  const defs = allToolDefs(userId, sessionId);
  return createSdkMcpServer({
    name: MCP_SERVER_NAME,
    // Each tool has a distinct schema; Object.values collapses them into a
    // union that the generic registerTool can't narrow. Cast through unknown.
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    tools: Object.values(defs).map((t) => registerTool(t as ToolDef<any>)),
  });
}

/** SDK-prefixed tool names that a given sub-agent should be allowed to call.
 * Combines ToolDef.subagents tagging with the external-MCP whitelist above. */
export function subagentToolNames(tag: SubagentTag, userId: string, sessionId?: string): string[] {
  const defs = Object.values(allToolDefs(userId, sessionId)) as TaggedTool[];
  const fromDefs = defs.filter((t) => t.subagents?.includes(tag)).map((t) => mcpName(t.name));
  return [...fromDefs, ...EXTERNAL_MCP_TOOLS_BY_SUBAGENT[tag]];
}
