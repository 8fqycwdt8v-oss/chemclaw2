import type { Options } from '@anthropic-ai/claude-agent-sdk';
import { scopedSessionStore } from '@chemclaw2/db/session-store';
import { checkToolInput, checkToolOutput } from '@chemclaw2/agent-tools';
import { resolveToolMode, checkBudgetWouldExceed, incrementSpend, getProjectBudget } from '@chemclaw2/db';
import { buildInProcessMcpServer } from './sdk-tools';
import { loadSkillsBlock } from './skills';

// v2.1-D: tools that count against the experiments_cap. Everything else only
// counts against tool_calls_cap.
const EXPERIMENT_TOOLS = new Set(['kickoff_campaign']);

const BASE_SYSTEM_PROMPT = `You are ChemClaw, a pharma R&D knowledge-intelligence assistant.
You have access to an organization knowledge base, compound registry, and reaction database.
Always cite your sources. Never fabricate CAS numbers, yields, or experimental conditions.
When uncertain, say so explicitly rather than guessing.`;

export function buildQueryOptions(sessionId: string, userId: string): Options {
  // Skills are loaded from disk per request so newly-saved skills are visible
  // without a process restart (followup #10).
  const systemPrompt = BASE_SYSTEM_PROMPT + loadSkillsBlock();
  // v2.1-D: budgets are keyed by the same projectKey the session store uses, so
  // per-user spend rolls up under the same identity as session ownership.
  const projectKey = `chemclaw2:${userId}`;

  // scopedSessionStore forces projectKey = chemclaw2:<userId> on every store call,
  // ensuring sessions are isolated per user regardless of the SDK's cwd-derived default.
  return {
    systemPrompt,
    sessionStore: scopedSessionStore(`chemclaw2:${userId}`),
    resume: sessionId,
    mcpServers: {
      'chemclaw2-tools': buildInProcessMcpServer(userId, sessionId),
      'mcp-molfp': {
        type: 'stdio',
        command: 'python',
        args: ['-m', 'mcp_molfp.server'],
      },
      'mcp-rxnfp': {
        type: 'stdio',
        command: 'python',
        args: ['-m', 'mcp_rxnfp.server'],
      },
    },
    hooks: {
      PreToolUse: [
        {
          hooks: [
            async (input) => {
              if (input.hook_event_name !== 'PreToolUse') return {};

              // v2.1-D2: budget gate. Runs before the permission check so a
              // capped-out project can't accidentally grant itself another
              // experiment by setting a per-tool override. Errors in the budget
              // lookup fail open (allow) to avoid taking the agent down on a
              // missing/misconfigured budgets table.
              const isExperiment = EXPERIMENT_TOOLS.has(input.tool_name);
              const exceeded = await checkBudgetWouldExceed(projectKey, {
                toolCalls: 1,
                experiments: isExperiment ? 1 : 0,
              }).catch(() => null);
              if (exceeded) {
                const reason =
                  `Budget cap reached: ${exceeded.exceeded} (${exceeded.current}/${exceeded.cap}). ` +
                  `Wait for the period to roll over or ask an admin to raise the cap.`;
                return {
                  decision: 'block',
                  reason,
                  hookSpecificOutput: {
                    hookEventName: 'PreToolUse',
                    permissionDecision: 'deny',
                    permissionDecisionReason: reason,
                  },
                };
              }

              // J2: per-tool authorization. The deny path short-circuits before
              // the redaction check runs — saves the redaction work on a tool
              // we'd never allow anyway.
              const mode = await resolveToolMode(input.tool_name, userId).catch(() => 'allow' as const);
              if (mode === 'deny') {
                const reason = `Tool '${input.tool_name}' is denied for this user by tool_permissions.`;
                return {
                  decision: 'block',
                  reason,
                  hookSpecificOutput: {
                    hookEventName: 'PreToolUse',
                    permissionDecision: 'deny',
                    permissionDecisionReason: reason,
                  },
                };
              }
              if (mode === 'ask') {
                // Surface as a permission ask the chat UI can render as a confirm
                // card. (G1's plan-mode preset uses a prompt-engineered version;
                // this is the SDK-native path.)
                return {
                  hookSpecificOutput: {
                    hookEventName: 'PreToolUse',
                    permissionDecision: 'ask',
                    permissionDecisionReason: `Tool '${input.tool_name}' requires confirmation per tool_permissions.`,
                  },
                };
              }

              const res = checkToolInput(
                input.tool_name,
                (input.tool_input ?? {}) as Record<string, unknown>,
              );
              if (res.action === 'block') {
                return {
                  decision: 'block',
                  reason: res.reason,
                  hookSpecificOutput: {
                    hookEventName: 'PreToolUse',
                    permissionDecision: 'deny',
                    permissionDecisionReason: res.reason,
                  },
                };
              }
              if (res.input) {
                return {
                  hookSpecificOutput: {
                    hookEventName: 'PreToolUse',
                    updatedInput: res.input,
                  },
                };
              }
              return {};
            },
          ],
        },
      ],
      PostToolUse: [
        {
          hooks: [
            async (input) => {
              if (input.hook_event_name !== 'PostToolUse') return {};

              // v2.1-D3: accumulate spend after a successful tool call. If no
              // budget is configured we skip the increment write entirely —
              // measuring users who never set a cap is dead weight.
              const budget = await getProjectBudget(projectKey).catch(() => null);
              if (budget) {
                const isExperiment = EXPERIMENT_TOOLS.has(input.tool_name);
                await incrementSpend(projectKey, budget.period, {
                  toolCalls: 1,
                  experiments: isExperiment ? 1 : 0,
                }).catch((err) => {
                  console.error('[agent] incrementSpend failed:', err);
                });
              }

              const text =
                typeof input.tool_response === 'string'
                  ? input.tool_response
                  : JSON.stringify(input.tool_response ?? '');
              const { warnings } = await checkToolOutput(input.tool_name, text);
              if (warnings.length === 0) return {};
              return {
                hookSpecificOutput: {
                  hookEventName: 'PostToolUse',
                  additionalContext: 'Verification warnings: ' + warnings.join('; '),
                },
              };
            },
          ],
        },
      ],
    },
  };
}
