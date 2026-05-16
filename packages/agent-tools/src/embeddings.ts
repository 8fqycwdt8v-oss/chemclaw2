/**
 * Shared embedding constants and pre-processing helpers.
 *
 * Both apps/web and workers/fp-worker call OpenAI's embedding API; this module
 * is the single source of truth for the model, the dimension (which must match
 * the `vector(1536)` columns in migrations 0003), the per-input char limit, and
 * the markdown-stripping pre-process.
 *
 * The OpenAI client itself stays in the calling app — agent-tools does not take
 * a network-IO dep.
 */

export const EMBED_MODEL = 'text-embedding-3-small';
export const EMBED_DIM = 1536;
// text-embedding-3-small supports 8191 tokens. 6000 chars is conservative for
// chemistry text (~1.3 chars/token); rare individual inputs that exceed this
// are truncated rather than rejected — losing the tail beats failing the call.
export const EMBED_CHAR_LIMIT = 6000;

/**
 * Strip markdown markers that the embedding model would otherwise waste tokens
 * on. SMILES strings are deliberately left untouched: SMILES never contains the
 * paired markers `**`/`__` or backticks, so the patterns below cannot collide
 * with chemistry content. Single-asterisk italics are NOT stripped (SMILES
 * uses `*` as a wildcard atom).
 */
export function stripMarkdownForEmbedding(text: string): string {
  return text
    // Header line-start markers ("# Title" → "Title")
    .replace(/^#{1,6}[ \t]+/gm, '')
    // Bold (paired markers — no collision with SMILES)
    .replace(/\*\*([^*]+?)\*\*/g, '$1')
    .replace(/__([^_]+?)__/g, '$1')
    // Inline code (paired backticks — no collision with SMILES)
    .replace(/`([^`]+)`/g, '$1')
    // Fenced code blocks: strip the fence lines but keep the inner code
    .replace(/^```[a-zA-Z0-9_-]*\n?|\n?^```$/gm, '')
    // Bullet markers at line-start
    .replace(/^[ \t]*[-+][ \t]+/gm, '')
    // Numbered list markers
    .replace(/^[ \t]*\d+\.[ \t]+/gm, '')
    // Blockquote markers
    .replace(/^>[ \t]?/gm, '')
    // HTML tags
    .replace(/<[^>]+>/g, '')
    // Collapse runs of blank lines so the chunker's \n{2,} split stays clean
    .replace(/\n{3,}/g, '\n\n');
}

/**
 * Prepare an array of inputs for the OpenAI embedding API.
 *
 * Validates that every input is a non-empty string (throws — empty inputs would
 * desync the returned vector array from the caller's indices, which can lead to
 * silently mis-paired chunks and embeddings). Truncates over-long inputs.
 *
 * Returns the array in the SAME ORDER and LENGTH as the input. Caller can map
 * vectors back to chunks by index.
 */
export function prepareEmbeddingInputs(texts: string[]): string[] {
  const out: string[] = new Array(texts.length);
  for (let i = 0; i < texts.length; i++) {
    const t = texts[i];
    if (typeof t !== 'string') {
      throw new Error(`prepareEmbeddingInputs: element ${i} is not a string (${typeof t})`);
    }
    if (t.trim().length === 0) {
      throw new Error(`prepareEmbeddingInputs: element ${i} is empty/whitespace`);
    }
    out[i] = t.length > EMBED_CHAR_LIMIT ? t.slice(0, EMBED_CHAR_LIMIT) : t;
  }
  return out;
}
