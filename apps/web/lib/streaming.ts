import { query } from '@anthropic-ai/claude-agent-sdk';
import type { Options } from '@anthropic-ai/claude-agent-sdk';

const encoder = new TextEncoder();

function sseEvent(data: unknown): Uint8Array {
  return encoder.encode(`data: ${JSON.stringify(data)}\n\n`);
}

export async function* runAgentQuery(
  prompt: string,
  options: Options,
): AsyncGenerator<Uint8Array> {
  try {
    for await (const event of query({ prompt, options })) {
      yield sseEvent(event);
    }
  } finally {
    yield encoder.encode('data: [DONE]\n\n');
  }
}

export function agentToStream(prompt: string, options: Options): ReadableStream<Uint8Array> {
  return new ReadableStream({
    async start(controller) {
      try {
        for await (const chunk of runAgentQuery(prompt, options)) {
          controller.enqueue(chunk);
        }
      } catch (err) {
        const errEvent = sseEvent({ type: 'error', message: String(err) });
        controller.enqueue(errEvent);
      } finally {
        controller.close();
      }
    },
  });
}
