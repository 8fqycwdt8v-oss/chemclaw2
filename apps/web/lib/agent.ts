import type { Options } from '@anthropic-ai/claude-agent-sdk';
import { SYSTEM_PROMPT_DYNAMIC_BOUNDARY } from '@anthropic-ai/claude-agent-sdk';
import { scopedSessionStore } from '@chemclaw2/db/session-store';
import { checkToolInput, checkToolOutput, checkUserPrompt } from '@chemclaw2/agent-tools';
import {
  resolveToolMode,
  getBudgetWithSpend,
  incrementSpend,
  type BudgetWithSpend,
} from '@chemclaw2/db';
import { buildInProcessMcpServer } from './sdk-tools';
import { loadSkillsBlock } from './skills';

// v2.1-D: tools that count against the experiments_cap. Everything else only
// counts against tool_calls_cap.
const EXPERIMENT_TOOLS = new Set(['kickoff_campaign']);

const BASE_SYSTEM_PROMPT = `You are ChemClaw, a pharma R&D knowledge-intelligence assistant.
You have access to an organization knowledge base, compound registry, and reaction database.
Always cite your sources. Never fabricate CAS numbers, yields, or experimental conditions.
When uncertain, say so explicitly rather than guessing.`;

// Wave-1 A3: surface model + turn cap as env so operators can tune without
// redeploying. SDK defaults are good but invisible; an explicit value is
// auditable. Sonnet 4.6 matches the chemistry-reasoning weight we target.
const DEFAULT_MODEL = process.env.ANTHROPIC_MODEL ?? 'claude-sonnet-4-6';
const DEFAULT_MAX_TURNS = Number(process.env.AGENT_MAX_TURNS ?? '50');

export type QueryOptionsExtras = {
  /** Wave-1 A1: request plan-mode for this turn — no tools execute. */
  planMode?: boolean;
};

export function buildQueryOptions(
  sessionId: string,
  userId: string,
  extras: QueryOptionsExtras = {},
): Options {
  // Skills are loaded from disk per request so newly-saved skills are visible
  // without a process restart (followup #10).
  //
  // Wave-1 A2: split systemPrompt across SYSTEM_PROMPT_DYNAMIC_BOUNDARY so the
  // static base prefix is eligible for cross-session prompt caching on models
  // that support it (Sonnet 4.6+). Skills change per user / per disk-edit, so
  // they live AFTER the boundary. When no skills are loaded, pass the static
  // string directly — no boundary needed and the whole prompt caches.
  const skillsBlock = loadSkillsBlock();
  const systemPrompt: Options['systemPrompt'] = skillsBlock
    ? [BASE_SYSTEM_PROMPT, SYSTEM_PROMPT_DYNAMIC_BOUNDARY, skillsBlock]
    : BASE_SYSTEM_PROMPT;
  // v2.1-D: budgets are keyed by the same projectKey the session store uses, so
  // per-user spend rolls up under the same identity as session ownership.
  const projectKey = `chemclaw2:${userId}`;

  // Wave-1 D1: one budget lookup per request, cached for the lifetime of the
  // query closure. PreToolUse and PostToolUse both call getBudget() — the
  // promise is shared, so only the first call hits the DB. localSpend tracks
  // increments accumulated WITHIN this request so the cap check stays accurate
  // across multiple tool calls in the same turn (the DB row would otherwise
  // still show start-of-request spend).
  let budgetCache: Promise<BudgetWithSpend | null> | undefined;
  const getBudget = (): Promise<BudgetWithSpend | null> => {
    if (!budgetCache) {
      budgetCache = getBudgetWithSpend(projectKey).catch((err) => {
        console.error('[agent] budget lookup failed:', err);
        return null;
      });
    }
    return budgetCache;
  };
  const localSpend = { toolCalls: 0, experiments: 0 };

  // scopedSessionStore forces projectKey = chemclaw2:<userId> on every store call,
  // ensuring sessions are isolated per user regardless of the SDK's cwd-derived default.
  return {
    systemPrompt,
    sessionStore: scopedSessionStore(`chemclaw2:${userId}`),
    resume: sessionId,
    model: DEFAULT_MODEL,
    maxTurns: DEFAULT_MAX_TURNS,
    // Wave-1 A1: native plan mode. Replaces the prompt-engineered
    // `[PLAN MODE]` prefix that ChatClient used to send. When true the SDK
    // blocks tool execution entirely; the agent must present a plan and the
    // user re-sends without planMode to actually run it.
    ...(extras.planMode ? { permissionMode: 'plan' as const } : {}),
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
      // Wave-3a A4: structured lifecycle logs for ops + tracing. SessionStart
      // fires once per fresh-start or resume; SessionEnd fires when the SDK
      // tears down the session. Persistence of per-session aggregates lives
      // in project_budget_spend already; these logs anchor the bookends for
      // log-search and OpenTelemetry correlation.
      SessionStart: [
        {
          hooks: [
            async (input) => {
              if (input.hook_event_name !== 'SessionStart') return {};
              console.log('[agent] session start', {
                session_id: input.session_id,
                source: input.source,
                model: input.model,
                user_id: userId,
              });
              return {};
            },
          ],
        },
      ],
      SessionEnd: [
        {
          hooks: [
            async (input) => {
              if (input.hook_event_name !== 'SessionEnd') return {};
              console.log('[agent] session end', {
                session_id: input.session_id,
                reason: input.reason,
                user_id: userId,
              });
              return {};
            },
          ],
        },
      ],
      // Wave-3a A5: redaction on the user's free-text prompt before the model
      // sees it. The tool-input path (`checkToolInput`) only covered prompts
      // the agent CONSTRUCTED — a user typing "my SSN is 123-45-6789" went
      // straight to the LLM. SSN-like patterns now block with a clear
      // resubmit message. Controlled-substance terms are still gated upstream
      // in the chat route by scheduledSubstanceGate to keep that decision
      // override-able with justification.
      UserPromptSubmit: [
        {
          hooks: [
            async (input) => {
              if (input.hook_event_name !== 'UserPromptSubmit') return {};
              const verdict = checkUserPrompt(input.prompt);
              if (verdict.action === 'block') {
                return {
                  decision: 'block',
                  reason: verdict.reason,
                  hookSpecificOutput: {
                    hookEventName: 'UserPromptSubmit',
                    suppressOriginalPrompt: true,
                  },
                };
              }
              return {};
            },
          ],
        },
      ],
      PreToolUse: [
        {
          hooks: [
            async (input) => {
              if (input.hook_event_name !== 'PreToolUse') return {};

              // v2.1-D2 + Wave-1 D1: budget gate. Runs before the permission
              // check so a capped-out project can't accidentally grant itself
              // another experiment by setting a per-tool override. Budget is
              // fetched once per request via getBudget() (single round-trip
              // LEFT JOIN); subsequent calls in the same turn hit the cache.
              // localSpend tracks in-request increments so the cap check
              // remains accurate even though the DB row is from request start.
              // Fail-open on lookup error to avoid taking the agent down on a
              // missing/misconfigured budgets table.
              const isExperiment = EXPERIMENT_TOOLS.has(input.tool_name);
              const budgetInfo = await getBudget();
              if (budgetInfo) {
                const { budget, spend } = budgetInfo;
                const projectedTool = spend.toolCalls + localSpend.toolCalls + 1;
                const projectedExp =
                  spend.experiments + localSpend.experiments + (isExperiment ? 1 : 0);
                // Wave-2c: deny new tool calls when the token cap is already
                // breached. Per-tool tokens are unknown ahead of time (only
                // billed at end-of-stream), so we just hard-stop when the
                // bucket is already over.
                let exceeded: { kind: 'tool_calls' | 'experiments' | 'tokens'; cap: number; current: number } | null = null;
                if (budget.toolCallsCap != null && projectedTool > budget.toolCallsCap) {
                  exceeded = { kind: 'tool_calls', cap: budget.toolCallsCap, current: spend.toolCalls + localSpend.toolCalls };
                } else if (budget.experimentsCap != null && projectedExp > budget.experimentsCap) {
                  exceeded = { kind: 'experiments', cap: budget.experimentsCap, current: spend.experiments + localSpend.experiments };
                } else if (budget.tokensCap != null && spend.tokens >= budget.tokensCap) {
                  exceeded = { kind: 'tokens', cap: budget.tokensCap, current: spend.tokens };
                }
                if (exceeded) {
                  const reason =
                    `Budget cap reached: ${exceeded.kind} (${exceeded.current}/${exceeded.cap}). ` +
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

              // v2.1-D3 + Wave-1 D1: accumulate spend after every tool
              // invocation (success or error — the cost has already been paid).
              // Re-uses the cached budget config from PreToolUse, so no
              // additional DB read; bumps localSpend in lock-step with the DB
              // increment so the next PreToolUse cap check sees fresh state.
              const budgetInfo = await getBudget();
              if (budgetInfo) {
                const isExperiment = EXPERIMENT_TOOLS.has(input.tool_name);
                localSpend.toolCalls += 1;
                if (isExperiment) localSpend.experiments += 1;
                await incrementSpend(projectKey, budgetInfo.budget.period, {
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
