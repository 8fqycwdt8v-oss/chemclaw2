import OpenAI from 'openai';
import {
  EMBED_MODEL,
  EMBED_DIM,
  prepareEmbeddingInputs,
  stripMarkdownForEmbedding,
} from '@chemclaw2/agent-tools';

let client: OpenAI | undefined;

function getClient() {
  if (!client) {
    const apiKey = process.env.OPENAI_API_KEY;
    if (!apiKey) throw new Error('OPENAI_API_KEY is required for embeddings');
    client = new OpenAI({ apiKey });
  }
  return client;
}

export async function embedText(text: string): Promise<number[]> {
  const [embedding] = await embedTexts([stripMarkdownForEmbedding(text)]);
  return embedding;
}

/**
 * Batch embed multiple texts in a single API call. Markdown markup is stripped
 * before the call so the model doesn't waste tokens on `**`/`#`/backticks etc.
 * Returns vectors in the same order and length as the input — empty/whitespace
 * inputs throw rather than silently desync the index alignment.
 *
 * Wave-1 D4: cap each upstream request at MAX_BATCH chunks. A 500k-char wiki
 * page yields ~400 chunks; without the cap a single request approaches the
 * OpenAI per-call limit and any future chunker change that lifts the chunk
 * count would silently break. Split-and-merge keeps order intact.
 */
const MAX_BATCH = 100;

export async function embedTexts(texts: string[]): Promise<number[][]> {
  if (texts.length === 0) return [];
  const stripped = texts.map(stripMarkdownForEmbedding);
  const inputs = prepareEmbeddingInputs(stripped);
  const out: number[][] = [];
  for (let start = 0; start < inputs.length; start += MAX_BATCH) {
    const batch = inputs.slice(start, start + MAX_BATCH);
    const res = await getClient().embeddings.create({ model: EMBED_MODEL, input: batch });
    if (res.data.length !== batch.length) {
      throw new Error(`embedTexts: model returned ${res.data.length} vectors for ${batch.length} inputs`);
    }
    for (const d of res.data) {
      if (d.embedding.length !== EMBED_DIM) {
        throw new Error(`embedTexts: vector dim ${d.embedding.length} ≠ expected ${EMBED_DIM}`);
      }
      out.push(d.embedding);
    }
  }
  return out;
}
