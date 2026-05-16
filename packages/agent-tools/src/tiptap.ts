/**
 * Shallow shape check for a Tiptap doc payload. We don't validate every node
 * type — Tiptap's own renderer drops unknown nodes — but we reject obviously
 * malformed inputs that would crash the editor on next load (top-level type
 * must be 'doc', content must be an array).
 *
 * Canonical home for the validator. apps/web/lib/validation.ts re-exports
 * this so route handlers and tool factories share one source.
 */
export function isValidTiptapDoc(value: unknown): value is { type: 'doc'; content: unknown[] } {
  if (!value || typeof value !== 'object') return false;
  const v = value as Record<string, unknown>;
  if (v.type !== 'doc') return false;
  if (!Array.isArray(v.content)) return false;
  return true;
}
