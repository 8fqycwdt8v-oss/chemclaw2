import type { z } from 'zod';

/**
 * Wave-3g A6: single source of truth for tool input schemas. Each tool
 * factory declares its schema as a Zod raw shape (dict of Zod field types);
 * the SDK's `tool(...)` helper takes the same shape directly, so apps/web's
 * sdk-tools.ts no longer re-declares it.
 *
 * Two big wins beyond LOC reduction:
 * - The agent-side `execute(input)` is now type-safe against the schema via
 *   `z.infer<z.ZodObject<S>>` — change the schema, the execute signature
 *   changes too, the type-checker catches the drift.
 * - JSON Schema in the tool factories was vestigial (no consumer outside
 *   sdk-tools.ts). Deleting it removes a class of drift bugs (e.g. the
 *   pre-Wave-3g `lookup_knowledge` had subtly different `types` enums in
 *   its JSON schema vs. the Zod surface used by the SDK).
 */
export type ZodRawShape = Record<string, z.ZodTypeAny>;

// The exact Zod-Object inference path. Helper alias keeps tool sites short.
export type ToolInput<S extends ZodRawShape> = z.infer<z.ZodObject<S>>;

export interface ToolDef<S extends ZodRawShape, R = unknown> {
  /** MCP-namespaced tool name. Must match the registered MCP tool name on
   *  the in-process server (see apps/web/lib/sdk-tools.ts). */
  name: string;
  /** Agent-facing description. Appears in the system prompt; keep it tight
   *  and tell the model WHEN to call this vs. an adjacent tool. */
  description: string;
  /** Zod raw shape passed to the SDK's tool() helper. */
  schema: S;
  /** Tool implementation. Input type is inferred from the schema. */
  execute: (input: ToolInput<S>) => Promise<R>;
}
