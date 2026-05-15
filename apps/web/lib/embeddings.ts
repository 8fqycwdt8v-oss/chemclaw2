import OpenAI from 'openai';

// text-embedding-3-small supports 8191 tokens; use 6000 chars as a conservative
// character limit (chemistry text is dense, ~1.3 chars/token on average).
const EMBED_CHAR_LIMIT = 6000;

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
  const [embedding] = await embedTexts([text]);
  return embedding;
}

/** Batch embed multiple texts in a single API call. */
export async function embedTexts(texts: string[]): Promise<number[][]> {
  const truncated = texts.map((t) => {
    if (t.length > EMBED_CHAR_LIMIT) {
      console.warn(`[embeddings] text truncated from ${t.length} to ${EMBED_CHAR_LIMIT} chars for embedding`);
      return t.slice(0, EMBED_CHAR_LIMIT);
    }
    return t;
  });
  const res = await getClient().embeddings.create({
    model: 'text-embedding-3-small',
    input: truncated,
  });
  // OpenAI returns embeddings in the same order as the input
  return res.data.map((d) => d.embedding);
}
