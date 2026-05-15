import type { Options } from '@anthropic-ai/claude-agent-sdk';
import { scopedSessionStore } from '@chemclaw2/db/session-store';

const SYSTEM_PROMPT = `You are ChemClaw, a pharma R&D knowledge-intelligence assistant.
You have access to an organization knowledge base, compound registry, and reaction database.
Always cite your sources. Never fabricate CAS numbers, yields, or experimental conditions.
When uncertain, say so explicitly rather than guessing.`;

export function buildQueryOptions(sessionId: string, userId: string): Options {
  // scopedSessionStore forces projectKey = chemclaw2:<userId> on every store call,
  // ensuring sessions are isolated per user regardless of the SDK's cwd-derived default.
  return {
    systemPrompt: SYSTEM_PROMPT,
    sessionStore: scopedSessionStore(`chemclaw2:${userId}`),
    resume: sessionId,
    mcpServers: {},
  };
}
