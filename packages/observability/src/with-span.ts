import { trace, SpanStatusCode, type Attributes, type Span } from '@opentelemetry/api';

const tracer = trace.getTracer('@chemclaw2/observability');

/**
 * Run `fn` inside a new span. The span is ended automatically; exceptions are
 * recorded on the span and rethrown. Attributes are flattened to OTel-compatible
 * primitives (strings/numbers/booleans only).
 */
export async function withSpan<T>(
  name: string,
  attributes: Attributes,
  fn: (span: Span) => Promise<T>,
): Promise<T> {
  return tracer.startActiveSpan(name, { attributes }, async (span) => {
    const start = Date.now();
    try {
      const result = await fn(span);
      span.setAttribute('duration_ms', Date.now() - start);
      return result;
    } catch (err) {
      span.setAttribute('duration_ms', Date.now() - start);
      span.recordException(err as Error);
      span.setStatus({ code: SpanStatusCode.ERROR, message: err instanceof Error ? err.message : String(err) });
      throw err;
    } finally {
      span.end();
    }
  });
}

export function withSpanSync<T>(
  name: string,
  attributes: Attributes,
  fn: (span: Span) => T,
): T {
  return tracer.startActiveSpan(name, { attributes }, (span) => {
    const start = Date.now();
    try {
      const result = fn(span);
      span.setAttribute('duration_ms', Date.now() - start);
      return result;
    } catch (err) {
      span.setAttribute('duration_ms', Date.now() - start);
      span.recordException(err as Error);
      span.setStatus({ code: SpanStatusCode.ERROR, message: err instanceof Error ? err.message : String(err) });
      throw err;
    } finally {
      span.end();
    }
  });
}
