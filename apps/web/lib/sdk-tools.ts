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
  type ToolDef,
  type ZodRawShape,
} from '@chemclaw2/agent-tools';
import { embedText, embedTexts } from './embeddings';

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

export function buildInProcessMcpServer(userId: string, sessionId?: string) {
  const campaign = createSynthesisCampaignTools(userId);
  const deepResearch = createDeepResearchTools(userId, embedTexts, sessionId);
  const contradiction = createContradictionTools(userId);

  return createSdkMcpServer({
    name: 'chemclaw2-tools',
    tools: [
      // Read tools — stateless, no userId needed.
      registerTool(compoundSimilaritySearchTool),
      registerTool(reactionSimilaritySearchTool),
      registerTool(createWikiFetchTool(embedText)),
      registerTool(hazardLookupTool),
      registerTool(greenSolventTool),
      registerTool(substructureCandidatesTool),
      registerTool(interpretAnalyticalResultTool),
      registerTool(lookupPropertiesTool),
      registerTool(createLookupKnowledgeTool(embedText)),

      // Persistence-bound external fetches — Wave-2a factory captures userId
      // so each result upserts into external_facts attributed correctly.
      registerTool(createWebSearchTool(userId)),
      registerTool(createDocFetchTool(userId)),
      registerTool(createElnFetchTool(userId)),

      // Wiki write tools.
      registerTool(createWikiUpsertTool(userId, embedTexts)),
      registerTool(createWikiProposeTool(userId)),

      // Wave-3e B6 entity-extractor write tools.
      registerTool(createRegisterPropertyTool(userId)),
      registerTool(createRegisterPaperTool(userId)),

      // Wave-3b sub-agent helper tools.
      registerTool(contradiction.readTwo),
      registerTool(contradiction.record),
      registerTool(deepResearch.begin),
      registerTool(deepResearch.finalize),

      // Synthesis-campaign workflow.
      registerTool(campaign.synthesisCampaignTool),
      registerTool(campaign.confirmSynthesisPlanTool),
      registerTool(campaign.kickoffCampaignTool),
    ],
  });
}
