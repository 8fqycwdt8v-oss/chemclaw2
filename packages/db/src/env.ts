import { z } from 'zod';

const EnvSchema = z.object({
  DATABASE_URL: z.string().min(1, 'DATABASE_URL is required'),
  DB_POOL_MAX: z
    .string()
    .optional()
    .transform((raw) => {
      if (!raw) return 15;
      const n = Number.parseInt(raw, 10);
      return Number.isFinite(n) && n > 0 && n <= 100 ? n : 15;
    }),
});

export type DbEnv = z.infer<typeof EnvSchema>;

let cached: DbEnv | undefined;

export function dbEnv(): DbEnv {
  if (!cached) cached = EnvSchema.parse(process.env);
  return cached;
}
