export const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
export const SLUG_MAX_LEN = 200;

export function isValidSlug(slug: string): boolean {
  return SLUG_RE.test(slug) && slug.length <= SLUG_MAX_LEN;
}

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
