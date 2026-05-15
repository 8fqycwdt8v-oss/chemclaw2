import type { Options, SessionStore } from '@anthropic-ai/claude-agent-sdk';
import { postgresSessionStore } from '@chemclaw2/db/session-store';

const SYSTEM_PROMPT = `You are ChemClaw, a pharma R&D knowledge-intelligence assistant.
You have access to an organization knowledge base, compound registry, and reaction database.
Always cite your sources. Never fabricate CAS numbers, yields, or experimental conditions.
When uncertain, say so explicitly rather than guessing.`;

// The DB store uses Record<string,unknown> entries; the SDK requires {type:string,...}.
// At runtime they are identical — all transcript entries carry a `type` field.
// We cast once here so the rest of the codebase stays clean.
const sessionStore = postgresSessionStore as unknown as SessionStore;

export function buildQueryOptions(sessionId: string, _userId: string): Options {
  return {
    systemPrompt: SYSTEM_PROMPT,
    sessionStore,
    resume: sessionId,
    mcpServers: {},
  };
}
