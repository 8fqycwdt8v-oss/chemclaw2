/**
 * Embedding dimension is shared between:
 *   - `vector(1536)` columns in migrations 0003 etc.
 *   - `wiki.ts` semantic search (dimension check + cast in SQL)
 *   - `@chemclaw2/agent-tools/embeddings` (re-exported as EMBED_DIM for the app/worker)
 *
 * Owning the constant in `@chemclaw2/db` keeps the value next to the schema
 * it must match. agent-tools re-exports for callers that want the symbol from
 * the tool package without taking a db dep transitively.
 */
export const EMBED_DIM = 1536;
