import { customType } from 'drizzle-orm/pg-core';

// pgvector 0.7+ supports HNSW on bit(N) — no 2000-dim cap for bit type
export const bit2048 = customType<{ data: string }>({
  dataType: () => 'bit(2048)',
});
