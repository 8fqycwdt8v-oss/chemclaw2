import { query } from '@anthropic-ai/claude-agent-sdk';
import type { Options } from '@anthropic-ai/claude-agent-sdk';
import { randomUUID } from 'crypto';

const encoder = new TextEncoder();
const LOOP_THRESHOLD = 3;

function sseEvent(data: unknown): Uint8Array {
  return encoder.encode(`data: ${JSON.stringify(data)}\n\n`);
}

/**
 * Detect a tight tool-call loop: same tool + identical input ≥ LOOP_THRESHOLD
 * times in a row. Returns the offending tool name, or null. Resets on any
 * different tool call or assistant text — natural agent variation is fine.
 */
function makeLoopDetector() {
  let lastKey: string | null = null;
  let repeats = 0;
  return (toolName: string, input: unknown): string | null => {
    const key = `${toolName}:${JSON.stringify(input)}`;
    if (key === lastKey) {
      repeats += 1;
      if (repeats >= LOOP_THRESHOLD) return toolName;
      return null;
    }
    lastKey = key;
    repeats = 1;
    return null;
  };
}

export async function* runAgentQuery(
  prompt: string,
  options: Options,
): AsyncGenerator<Uint8Array> {
  const detect = makeLoopDetector();
  for await (const event of query({ prompt, options })) {
    yield sseEvent(event);
    const message = (event as { message?: { content?: unknown[] } }).message;
    if (message && Array.isArray(message.content)) {
      for (const block of message.content as Array<Record<string, unknown>>) {
        if (block.type !== 'tool_use') continue;
        const looped = detect(String(block.name ?? ''), block.input);
        if (looped) {
          yield sseEvent({
            type: 'error',
            message: `Loop detected — ${looped} called ${LOOP_THRESHOLD} times in a row with identical input. Stopping.`,
          });
          return;
        }
      }
    }
  }
}

export function agentToStream(prompt: string, options: Options): ReadableStream<Uint8Array> {
  return new ReadableStream({
    async start(controller) {
      try {
        for await (const chunk of runAgentQuery(prompt, options)) {
          controller.enqueue(chunk);
        }
        // [DONE] only on success — clients stop reading at this sentinel
        controller.enqueue(encoder.encode('data: [DONE]\n\n'));
      } catch (err) {
        // Log full error server-side; send only a correlation ID to the client
        // to avoid leaking internal state (DB details, stack traces, API responses).
        const errorId = randomUUID();
        console.error({ errorId, err });
        controller.enqueue(sseEvent({ type: 'error', message: 'Request failed', errorId }));
      } finally {
        controller.close();
      }
    },
  });
}
