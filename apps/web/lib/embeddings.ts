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
 */
export async function embedTexts(texts: string[]): Promise<number[][]> {
  if (texts.length === 0) return [];
  const stripped = texts.map(stripMarkdownForEmbedding);
  const inputs = prepareEmbeddingInputs(stripped);
  const res = await getClient().embeddings.create({ model: EMBED_MODEL, input: inputs });
  if (res.data.length !== inputs.length) {
    throw new Error(`embedTexts: model returned ${res.data.length} vectors for ${inputs.length} inputs`);
  }
  return res.data.map((d) => {
    if (d.embedding.length !== EMBED_DIM) {
      throw new Error(`embedTexts: vector dim ${d.embedding.length} ≠ expected ${EMBED_DIM}`);
    }
    return d.embedding;
  });
}
