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
 * Batch embed multiple texts. Markdown markup is stripped before the call so
 * the model doesn't waste tokens on `**`/`#`/backticks etc. Returns vectors
 * in the same order and length as the input — empty/whitespace inputs throw
 * rather than silently desync the index alignment.
 *
 * Wave-1 D4: cap each upstream request at MAX_BATCH chunks. A 500k-char
 * wiki page yields ~400 chunks; without the cap a single request approaches
 * the OpenAI per-call limit.
 *
 * Wave-3h perf: batches now fire in parallel via Promise.all. Order is
 * preserved by indexing batches into their slots up-front. Previously the
 * sequential await chain held the wiki upsert connection open for 4 round-
 * trips on a 400-chunk page; parallel cuts that to one round-trip's latency.
 */
const MAX_BATCH = 100;

export async function embedTexts(texts: string[]): Promise<number[][]> {
  if (texts.length === 0) return [];
  const stripped = texts.map(stripMarkdownForEmbedding);
  const inputs = prepareEmbeddingInputs(stripped);

  const batches: string[][] = [];
  for (let start = 0; start < inputs.length; start += MAX_BATCH) {
    batches.push(inputs.slice(start, start + MAX_BATCH));
  }
  const client = getClient();
  const responses = await Promise.all(
    batches.map((batch) => client.embeddings.create({ model: EMBED_MODEL, input: batch })),
  );

  const out: number[][] = [];
  for (let i = 0; i < batches.length; i++) {
    const res = responses[i];
    const batch = batches[i];
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
