import { z } from 'zod';

/**
 * Single source of truth for app-level env vars. Parsed lazily on first
 * access so Next.js can statically analyse routes without every var set in
 * the build container. Each consumer (instrumentation, embeddings, agent
 * config, the Clerk webhook) calls `webEnv()` and reads the typed fields.
 */
const EnvSchema = z.object({
  // Observability
  LANGFUSE_PUBLIC_KEY: z.string().optional(),
  LANGFUSE_SECRET_KEY: z.string().optional(),
  LANGFUSE_BASEURL: z.string().url().default('https://cloud.langfuse.com'),

  // LLM
  OPENAI_API_KEY: z.string().optional(),
  ANTHROPIC_MODEL: z.string().default('claude-sonnet-4-6'),
  AGENT_MAX_TURNS: z
    .string()
    .optional()
    .transform((raw) => {
      const n = Number(raw ?? '50');
      return Number.isFinite(n) && n > 0 ? n : 50;
    }),

  // Clerk webhook signing secret (Clerk's verifyWebhook reads this env var
  // directly via @clerk/backend/webhooks). Optional in dev so /sign-in works
  // without a webhook configured; the webhook route returns 500 if missing
  // at request time, which Clerk retries with exponential backoff.
  CLERK_WEBHOOK_SIGNING_SECRET: z.string().optional(),

  NODE_ENV: z.enum(['development', 'production', 'test']).default('development'),
});

export type WebEnv = z.infer<typeof EnvSchema>;

let cached: WebEnv | undefined;

export function webEnv(): WebEnv {
  if (!cached) cached = EnvSchema.parse(process.env);
  return cached;
}
