import type { Options } from '@anthropic-ai/claude-agent-sdk';
import { scopedSessionStore } from '@chemclaw2/db/session-store';
import { checkToolInput, checkToolOutput } from '@chemclaw2/agent-tools';
import { resolveToolMode } from '@chemclaw2/db';
import { buildInProcessMcpServer } from './sdk-tools';
import { loadSkillsBlock } from './skills';

const BASE_SYSTEM_PROMPT = `You are ChemClaw, a pharma R&D knowledge-intelligence assistant.
You have access to an organization knowledge base, compound registry, and reaction database.
Always cite your sources. Never fabricate CAS numbers, yields, or experimental conditions.
When uncertain, say so explicitly rather than guessing.`;

// Skills are filesystem markdown packs (one directory per skill under skills/).
// They are loaded once on first agent build and concatenated onto the system prompt.
const SYSTEM_PROMPT = BASE_SYSTEM_PROMPT + loadSkillsBlock();

export function buildQueryOptions(sessionId: string, userId: string): Options {
  // scopedSessionStore forces projectKey = chemclaw2:<userId> on every store call,
  // ensuring sessions are isolated per user regardless of the SDK's cwd-derived default.
  return {
    systemPrompt: SYSTEM_PROMPT,
    sessionStore: scopedSessionStore(`chemclaw2:${userId}`),
    resume: sessionId,
    mcpServers: {
      'chemclaw2-tools': buildInProcessMcpServer(userId),
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
