import path from 'node:path';
import type { Options } from '@anthropic-ai/claude-agent-sdk';
import { scopedSessionStore } from '@chemclaw2/db/session-store';
import { getBudgetWithSpend, type BudgetWithSpend } from '@chemclaw2/db';
import { logger } from '@chemclaw2/observability';
import { buildInProcessMcpServer, subagentToolNames } from './sdk-tools';
import { buildHooks } from './agent-hooks';
import { DEEP_RESEARCH_PROMPT, CONTRADICTION_RESOLVER_PROMPT } from './subagent-prompts';
import { webEnv } from './env';

// Repo root containing .claude/skills/. Production cwd is /app (Dockerfile
// WORKDIR); dev cwd is apps/web/, hence the two-level fallback. The SDK
// auto-discovers SKILL.md files under <cwd>/.claude/skills/ when
// settingSources includes 'project'.
const PROJECT_ROOT = process.env.PROJECT_ROOT ?? path.resolve(process.cwd(), '..', '..');

const BASE_SYSTEM_PROMPT = `You are ChemClaw, a pharma R&D knowledge-intelligence assistant.
You have access to an organization knowledge base, compound registry, and reaction database.
Always cite your sources. Never fabricate CAS numbers, yields, or experimental conditions.
When uncertain, say so explicitly rather than guessing.

For comprehensive, multi-section investigations, prefer dispatching to a
sub-agent via the Task tool with subagent_type='deep-research'. The sub-agent
runs in isolated context with retrieval tools only and returns a structured
markdown report — you then persist it via finalize_deep_research.

For citation-conflict resolution on a wiki page, dispatch
subagent_type='contradiction-resolver'. The sub-agent reads both citations
and the chunks that reference them, weighs the evidence, and returns a
proposed winner + reason that you persist via record_contradiction.`;

// Sub-agent definitions. Each runs in isolated context with a restricted tool
// surface, derived from ToolDef.subagents tagging in agent-tools — no
// hand-rolled allowlists.
function buildSubagentDefinitions(userId: string, sessionId?: string): NonNullable<Options['agents']> {
  return {
    'deep-research': {
      description:
        'Multi-section research investigations. Use when the user asks for a comprehensive ' +
        'review, a structured report, or any "everything we know about X" question that needs ' +
        'to be persisted as a wiki page. Returns the report body as markdown for the parent to ' +
        'pass to finalize_deep_research.',
      prompt: DEEP_RESEARCH_PROMPT,
      tools: subagentToolNames('deep-research', userId, sessionId),
      mcpServers: ['chemclaw2-tools'],
      maxTurns: 30,
    },
    'contradiction-resolver': {
      description:
        'Weigh two conflicting citations on a wiki page and propose which is better supported. ' +
        'Use after the user (or another agent) identifies a citation dispute. Returns ' +
        'WINNER + REASON for the parent to persist via record_contradiction.',
      prompt: CONTRADICTION_RESOLVER_PROMPT,
      tools: subagentToolNames('contradiction-resolver', userId, sessionId),
      mcpServers: ['chemclaw2-tools'],
      maxTurns: 10,
    },
  };
}

const { ANTHROPIC_MODEL: DEFAULT_MODEL, AGENT_MAX_TURNS: DEFAULT_MAX_TURNS } = webEnv();

export type QueryOptionsExtras = {
  /** Request plan-mode for this turn — no tools execute. */
  planMode?: boolean;
};

export function buildQueryOptions(
  sessionId: string,
  userId: string,
  extras: QueryOptionsExtras = {},
): Options {
  // Budgets are keyed by the same projectKey the session store uses, so
  // per-user spend rolls up under the same identity as session ownership.
  const projectKey = `chemclaw2:${userId}`;

  // One budget lookup per request, cached for the lifetime of the query
  // closure. PreToolUse/PostToolUse hooks share the same promise.
  let budgetCache: Promise<BudgetWithSpend | null> | undefined;
  const getBudget = (): Promise<BudgetWithSpend | null> => {
    if (!budgetCache) {
      budgetCache = getBudgetWithSpend(projectKey).catch((err) => {
        logger.error('budget_lookup_failed', { project_key: projectKey, user_id: userId }, err);
        return null;
      });
    }
    return budgetCache;
  };
  const localSpend = { toolCalls: 0, experiments: 0 };

  return {
    systemPrompt: BASE_SYSTEM_PROMPT,
    // scopedSessionStore forces projectKey = chemclaw2:<userId> on every
    // store call, ensuring sessions are isolated per user regardless of the
    // SDK's cwd-derived default.
    sessionStore: scopedSessionStore(`chemclaw2:${userId}`),
    resume: sessionId,
    model: DEFAULT_MODEL,
    maxTurns: DEFAULT_MAX_TURNS,
    // Anchor cwd at the project root so the SDK discovers .claude/skills/
    // there regardless of where Node was launched from.
    cwd: PROJECT_ROOT,
    settingSources: ['project'],
    skills: 'all',
    // Native plan mode. When true the SDK blocks tool execution entirely;
    // the agent must present a plan and the user re-sends without planMode
    // to actually run it.
    ...(extras.planMode ? { permissionMode: 'plan' as const } : {}),
    // Sub-agent dispatch — the parent agent calls the Task tool with
    // subagent_type=<tag>. Each sub-agent's `tools` array narrows what it
    // may invoke; mcpServers mounts the same in-process server.
    agents: buildSubagentDefinitions(userId, sessionId),
    mcpServers: {
      'chemclaw2-tools': buildInProcessMcpServer(userId, sessionId),
      'mcp-molfp': { type: 'stdio', command: 'python', args: ['-m', 'mcp_molfp.server'] },
      'mcp-rxnfp': { type: 'stdio', command: 'python', args: ['-m', 'mcp_rxnfp.server'] },
    },
    hooks: buildHooks({ userId, projectKey, getBudget, localSpend }),
  };
}
