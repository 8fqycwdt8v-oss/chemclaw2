import { tool, createSdkMcpServer } from '@anthropic-ai/claude-agent-sdk';
import type { ToolDef, ZodRawShape, SubagentTag } from '@chemclaw2/agent-tools';
import { buildInProcessToolDefs, externalMcpToolsBySubagent } from './tool-registry';

const MCP_SERVER_NAME = 'chemclaw2-tools';
const mcpName = (toolName: string) => `mcp__${MCP_SERVER_NAME}__${toolName}`;

function toMcpText(result: unknown) {
  return { content: [{ type: 'text' as const, text: JSON.stringify(result) }] };
}

/**
 * One helper instead of per-tool boilerplate. Every tool factory exports
 * `{ name, description, schema, execute }` via the `ToolDef<S>` contract;
 * `registerTool` adapts that into the SDK's `tool(...)` shape and wraps the
 * response in MCP text. The agent-tools package owns the schema; this file
 * owns transport.
 */
function registerTool<S extends ZodRawShape>(t: ToolDef<S>) {
  // SDK's InferShape and our z.infer<z.ZodObject<S>> diverge at the type level
  // (two inference paths through the same Zod module) but produce identical
  // runtime shapes. Cast the handler arg to bridge.
  return tool(
    t.name,
    t.description,
    t.schema,
    // eslint-disable-next-line @typescript-eslint/no-explicit-any
    async (args) => toMcpText(await t.execute(args as any)),
  );
}

export function buildInProcessMcpServer(userId: string, sessionId?: string) {
  const defs = buildInProcessToolDefs(userId, sessionId);
  return createSdkMcpServer({
    name: MCP_SERVER_NAME,
    tools: defs.map((t) => registerTool(t)),
  });
}

/**
 * SDK-prefixed tool names a given sub-agent is allowed to call. Combines
 * `ToolDef.subagents` tagging (in-process tools) with the external-MCP
 * whitelist from tool-registry.
 */
export function subagentToolNames(tag: SubagentTag, userId: string, sessionId?: string): string[] {
  const defs = buildInProcessToolDefs(userId, sessionId);
  const fromDefs = defs
    .filter((t) => t.subagents?.includes(tag))
    .map((t) => mcpName(t.name));
  return [...fromDefs, ...externalMcpToolsBySubagent[tag]];
}
