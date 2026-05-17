import type { Options } from '@anthropic-ai/claude-agent-sdk';
import { checkToolInput, checkToolOutput, checkUserPrompt } from '@chemclaw2/agent-tools';
import {
  resolveToolMode,
  incrementSpend,
  type BudgetWithSpend,
} from '@chemclaw2/db';
import { experimentToolNames } from './tool-registry';

export type HookDeps = {
  userId: string;
  projectKey: string;
  /** One budget lookup per request, cached. PreToolUse and PostToolUse share it. */
  getBudget: () => Promise<BudgetWithSpend | null>;
  /** In-request spend accumulator. The DB row reflects start-of-request spend,
   * so we track local increments to keep the cap check accurate across tool calls. */
  localSpend: { toolCalls: number; experiments: number };
};

/**
 * Build the full hook bundle for `buildQueryOptions`. Each hook is a small
 * function with one responsibility; `buildHooks` wires them into the SDK's
 * verbose hooks shape. Splitting them out of agent.ts means the hook bodies
 * are testable in isolation and don't drown the query-options assembly.
 */
export function buildHooks(deps: HookDeps): NonNullable<Options['hooks']> {
  return {
    SessionStart: [{ hooks: [sessionStartHook(deps)] }],
    SessionEnd: [{ hooks: [sessionEndHook(deps)] }],
    UserPromptSubmit: [{ hooks: [userPromptSubmitHook()] }],
    PreToolUse: [{ hooks: [preToolUseHook(deps)] }],
    PostToolUse: [{ hooks: [postToolUseHook(deps)] }],
  };
}

/**
 * Structured lifecycle log. SessionStart fires once per fresh-start or resume;
 * SessionEnd fires when the SDK tears down the session. Persistence of
 * per-session aggregates lives in project_budget_spend already; these logs
 * anchor the bookends for log-search and OpenTelemetry correlation.
 */
function sessionStartHook({ userId }: HookDeps) {
  return async (input: Parameters<NonNullable<NonNullable<Options['hooks']>['SessionStart']>[number]['hooks'][number]>[0]) => {
    if (input.hook_event_name !== 'SessionStart') return {};
    console.log('[agent] session start', {
      session_id: input.session_id,
      source: input.source,
      model: input.model,
      user_id: userId,
    });
    return {};
  };
}

function sessionEndHook({ userId }: HookDeps) {
  return async (input: Parameters<NonNullable<NonNullable<Options['hooks']>['SessionEnd']>[number]['hooks'][number]>[0]) => {
    if (input.hook_event_name !== 'SessionEnd') return {};
    console.log('[agent] session end', {
      session_id: input.session_id,
      reason: input.reason,
      user_id: userId,
    });
    return {};
  };
}

/**
 * Redaction on the user's free-text prompt before the model sees it. The
 * tool-input path (`checkToolInput`) only covers prompts the agent
 * constructed — a user typing "my SSN is 123-45-6789" goes straight to the
 * LLM without this hook. Controlled-substance terms are still gated upstream
 * in the chat route to keep that decision override-able with justification.
 */
function userPromptSubmitHook() {
  return async (input: Parameters<NonNullable<NonNullable<Options['hooks']>['UserPromptSubmit']>[number]['hooks'][number]>[0]) => {
    if (input.hook_event_name !== 'UserPromptSubmit') return {};
    const verdict = checkUserPrompt(input.prompt);
    if (verdict.action === 'block') {
      return {
        decision: 'block' as const,
        reason: verdict.reason,
        hookSpecificOutput: {
          hookEventName: 'UserPromptSubmit' as const,
          suppressOriginalPrompt: true,
        },
      };
    }
    return {};
  };
}

/**
 * Composed pre-tool gate: budget cap → tool_permissions (deny/ask) →
 * input redaction. Order matters — a capped-out project must not be able
 * to evade the cap by reaching an ask/allow on a per-tool override.
 */
function preToolUseHook({ userId, getBudget, localSpend }: HookDeps) {
  return async (input: Parameters<NonNullable<NonNullable<Options['hooks']>['PreToolUse']>[number]['hooks'][number]>[0]) => {
    if (input.hook_event_name !== 'PreToolUse') return {};

    // Budget gate.
    const isExperiment = experimentToolNames.has(input.tool_name);
    const budgetInfo = await getBudget();
    if (budgetInfo) {
      const { budget, spend } = budgetInfo;
      const projectedTool = spend.toolCalls + localSpend.toolCalls + 1;
      const projectedExp = spend.experiments + localSpend.experiments + (isExperiment ? 1 : 0);
      // Per-tool tokens are unknown ahead of time (only billed at end-of-stream),
      // so we just hard-stop when the bucket is already over.
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
          decision: 'block' as const,
          reason,
          hookSpecificOutput: {
            hookEventName: 'PreToolUse' as const,
            permissionDecision: 'deny' as const,
            permissionDecisionReason: reason,
          },
        };
      }
    }

    // Per-tool authorization. The deny path short-circuits before redaction
    // runs — saves the redaction work on a tool we'd never allow anyway.
    const mode = await resolveToolMode(input.tool_name, userId).catch(() => 'allow' as const);
    if (mode === 'deny') {
      const reason = `Tool '${input.tool_name}' is denied for this user by tool_permissions.`;
      return {
        decision: 'block' as const,
        reason,
        hookSpecificOutput: {
          hookEventName: 'PreToolUse' as const,
          permissionDecision: 'deny' as const,
          permissionDecisionReason: reason,
        },
      };
    }
    if (mode === 'ask') {
      return {
        hookSpecificOutput: {
          hookEventName: 'PreToolUse' as const,
          permissionDecision: 'ask' as const,
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
        decision: 'block' as const,
        reason: res.reason,
        hookSpecificOutput: {
          hookEventName: 'PreToolUse' as const,
          permissionDecision: 'deny' as const,
          permissionDecisionReason: res.reason,
        },
      };
    }
    if (res.input) {
      return {
        hookSpecificOutput: {
          hookEventName: 'PreToolUse' as const,
          updatedInput: res.input,
        },
      };
    }
    return {};
  };
}

/**
 * After every tool invocation (success or error — the cost has already been
 * paid): accumulate spend, then run output verification. The DB write is
 * fire-and-forget so the next tool call isn't blocked by a round-trip;
 * localSpend updates synchronously so the in-process budget cap stays accurate.
 */
function postToolUseHook({ projectKey, getBudget, localSpend }: HookDeps) {
  return async (input: Parameters<NonNullable<NonNullable<Options['hooks']>['PostToolUse']>[number]['hooks'][number]>[0]) => {
    if (input.hook_event_name !== 'PostToolUse') return {};

    const budgetInfo = await getBudget();
    if (budgetInfo) {
      const isExperiment = experimentToolNames.has(input.tool_name);
      localSpend.toolCalls += 1;
      if (isExperiment) localSpend.experiments += 1;
      void incrementSpend(projectKey, budgetInfo.budget.period, {
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
        hookEventName: 'PostToolUse' as const,
        additionalContext: 'Verification warnings: ' + warnings.join('; '),
      },
    };
  };
}
