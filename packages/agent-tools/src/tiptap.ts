import { z } from 'zod';

/**
 * Shallow shape check for a Tiptap doc payload. We don't validate every node
 * type — Tiptap's own renderer drops unknown nodes — but we reject obviously
 * malformed inputs that would crash the editor on next load (top-level type
 * must be 'doc', content must be an array).
 *
 * Canonical home for the validator. apps/web/lib/validation.ts re-exports
 * this so route handlers and tool factories share one source.
 */
export const TiptapDocSchema = z.object({
  type: z.literal('doc'),
  content: z.array(z.unknown()),
});

export type TiptapDocShape = z.infer<typeof TiptapDocSchema>;

export function isValidTiptapDoc(value: unknown): value is TiptapDocShape {
  return TiptapDocSchema.safeParse(value).success;
}
