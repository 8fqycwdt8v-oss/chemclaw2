import { query } from '@anthropic-ai/claude-agent-sdk';
import type { Options } from '@anthropic-ai/claude-agent-sdk';
import { randomUUID } from 'crypto';
import { logger } from '@chemclaw2/observability';

const encoder = new TextEncoder();
const LOOP_THRESHOLD = 3;

/**
 * Wave-2c: SDK emits a single `result` message at end-of-stream containing
 * total usage + cost. The chat route uses this to persist token spend into
 * project_budget_spend.
 */
export type AgentStreamResult = {
  inputTokens: number;
  outputTokens: number;
  cacheReadTokens: number;
  cacheCreateTokens: number;
  totalCostUsd: number;
  isError: boolean;
};

export type AgentStreamOptions = {
  onResult?: (result: AgentStreamResult) => void | Promise<void>;
};

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

type SDKResultLike = {
  type: 'result';
  subtype?: string;
  is_error?: boolean;
  total_cost_usd?: number;
  usage?: {
    input_tokens?: number;
    output_tokens?: number;
    cache_read_input_tokens?: number;
    cache_creation_input_tokens?: number;
  };
};

function extractResult(event: unknown): AgentStreamResult | null {
  const e = event as SDKResultLike;
  if (!e || e.type !== 'result' || !e.usage) return null;
  return {
    inputTokens: e.usage.input_tokens ?? 0,
    outputTokens: e.usage.output_tokens ?? 0,
    cacheReadTokens: e.usage.cache_read_input_tokens ?? 0,
    cacheCreateTokens: e.usage.cache_creation_input_tokens ?? 0,
    totalCostUsd: e.total_cost_usd ?? 0,
    isError: e.is_error === true,
  };
}

export async function* runAgentQuery(
  prompt: string,
  options: Options,
  onResult?: (r: AgentStreamResult) => void,
): AsyncGenerator<Uint8Array> {
  const detect = makeLoopDetector();
  const startMs = Date.now();
  let firstChunk = true;
  let eventCount = 0;
  for await (const event of query({ prompt, options })) {
    if (firstChunk) {
      logger.info('agent_stream_ttfb', { ttfb_ms: Date.now() - startMs, session_id: options.resume });
      firstChunk = false;
    }
    eventCount++;
    yield sseEvent(event);
    const result = extractResult(event);
    if (result) {
      logger.info('agent_stream_complete', {
        session_id: options.resume,
        duration_ms: Date.now() - startMs,
        event_count: eventCount,
        input_tokens: result.inputTokens,
        output_tokens: result.outputTokens,
        total_cost_usd: result.totalCostUsd,
        is_error: result.isError,
      });
      if (onResult) onResult(result);
    }
    const message = (event as { message?: { content?: unknown[] } }).message;
    if (message && Array.isArray(message.content)) {
      for (const block of message.content as Array<Record<string, unknown>>) {
        if (block.type !== 'tool_use') continue;
        const looped = detect(String(block.name ?? ''), block.input);
        if (looped) {
          logger.warn('agent_loop_detected', {
            session_id: options.resume,
            tool: looped,
            threshold: LOOP_THRESHOLD,
          });
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

export function agentToStream(
  prompt: string,
  options: Options,
  streamOpts: AgentStreamOptions = {},
): ReadableStream<Uint8Array> {
  return new ReadableStream({
    async start(controller) {
      // Holder pattern so TS sees the inner callback assignment as a possible
      // mutation; declaring `let lastResult` directly narrows to `null` because
      // the closure assignment is invisible to the flow analysis.
      const sink: { value: AgentStreamResult | null } = { value: null };
      try {
        for await (const chunk of runAgentQuery(prompt, options, (r) => { sink.value = r; })) {
          controller.enqueue(chunk);
        }
        // [DONE] only on success — clients stop reading at this sentinel
        controller.enqueue(encoder.encode('data: [DONE]\n\n'));
      } catch (err) {
        // Log full error server-side; send only a correlation ID to the client
        // to avoid leaking internal state (DB details, stack traces, API responses).
        const errorId = randomUUID();
        logger.error('agent_stream_failed', { error_id: errorId, session_id: options.resume }, err);
        controller.enqueue(sseEvent({ type: 'error', message: 'Request failed', errorId }));
      } finally {
        // Wave-2c: surface end-of-stream usage to the caller for budget
        // accounting. Run AFTER the [DONE] sentinel is sent so a slow DB
        // write doesn't delay the client closing the connection.
        const finalResult = sink.value;
        if (finalResult && streamOpts.onResult) {
          try { await streamOpts.onResult(finalResult); }
          catch (err) {
            logger.error('on_result_callback_failed', {
              session_id: options.resume,
              input_tokens: finalResult.inputTokens,
              output_tokens: finalResult.outputTokens,
              total_cost_usd: finalResult.totalCostUsd,
            }, err);
          }
        }
        controller.close();
      }
    },
  });
}
