// Re-export from the canonical home in @chemclaw2/agent-tools so route
// handlers can continue importing from `@/lib/validation` (followup #18).
export { SLUG_RE, SLUG_MAX_LEN, RESERVED_SLUGS, isValidSlug } from '@chemclaw2/agent-tools';

// Wave-3f cut: UUID regex was defined inline in 11+ files. Lives in
// `@chemclaw2/agent-tools` (alongside slug.ts) so tool factories and apps/web
// share one canonical source.
export { UUID_RE, isUuid } from '@chemclaw2/agent-tools';

/**
 * Shallow shape check for a Tiptap doc payload from the editor. We don't
 * validate every node type — Tiptap's own renderer will drop unknown nodes —
 * but we reject obviously malformed inputs that would crash the editor on
 * next load (top-level type must be 'doc', content must be an array).
 */
export function isValidTiptapDoc(value: unknown): boolean {
  if (!value || typeof value !== 'object') return false;
  const v = value as Record<string, unknown>;
  if (v.type !== 'doc') return false;
  if (!Array.isArray(v.content)) return false;
  return true;
}
