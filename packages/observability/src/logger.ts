import { trace } from '@opentelemetry/api';
import { requestContext } from './request-context';

export type LogLevel = 'debug' | 'info' | 'warn' | 'error';

export type LogFields = Record<string, unknown>;

const LEVEL_PRIORITY: Record<LogLevel, number> = {
  debug: 10,
  info: 20,
  warn: 30,
  error: 40,
};

function minLevel(): number {
  const raw = process.env.LOG_LEVEL?.toLowerCase();
  if (raw && raw in LEVEL_PRIORITY) return LEVEL_PRIORITY[raw as LogLevel];
  return process.env.NODE_ENV === 'production' ? LEVEL_PRIORITY.info : LEVEL_PRIORITY.debug;
}

function serializeError(err: unknown): LogFields | undefined {
  if (err === undefined) return undefined;
  if (err instanceof Error) {
    return {
      name: err.name,
      message: err.message,
      stack: err.stack,
      ...(err.cause !== undefined && { cause: serializeError(err.cause) }),
    };
  }
  return { message: String(err) };
}

function emit(level: LogLevel, event: string, fields: LogFields, err?: unknown): void {
  if (LEVEL_PRIORITY[level] < minLevel()) return;

  const ctx = requestContext.get();
  const span = trace.getActiveSpan();
  const spanCtx = span?.spanContext();

  const record: LogFields = {
    level,
    time: new Date().toISOString(),
    event,
    ...(ctx?.requestId && { request_id: ctx.requestId }),
    ...(ctx?.userId && { user_id: ctx.userId }),
    ...(ctx?.sessionId && { session_id: ctx.sessionId }),
    ...(spanCtx?.traceId && { trace_id: spanCtx.traceId }),
    ...(spanCtx?.spanId && { span_id: spanCtx.spanId }),
    ...fields,
  };
  if (err !== undefined) {
    const serialized = serializeError(err);
    if (serialized) record.error = serialized;
  }

  // Mirror the event onto the active OTel span so Langfuse can correlate
  // log lines with the trace. attributes only accept primitives, so serialize
  // nested fields.
  if (span) {
    const attrs: Record<string, string | number | boolean> = { level };
    for (const [k, v] of Object.entries(fields)) {
      if (v == null) continue;
      if (typeof v === 'string' || typeof v === 'number' || typeof v === 'boolean') {
        attrs[k] = v;
      } else {
        attrs[k] = JSON.stringify(v).slice(0, 500);
      }
    }
    if (err !== undefined) {
      attrs.error_message = err instanceof Error ? err.message : String(err);
    }
    span.addEvent(event, attrs);
  }

  // JSON line on stdout/stderr — picked up by Fly/Axiom/Better Stack log shippers.
  const line = JSON.stringify(record);
  if (level === 'error' || level === 'warn') {
    process.stderr.write(line + '\n');
  } else {
    process.stdout.write(line + '\n');
  }
}

export type Logger = {
  debug(event: string, fields?: LogFields): void;
  info(event: string, fields?: LogFields): void;
  warn(event: string, fields?: LogFields, err?: unknown): void;
  error(event: string, fields?: LogFields, err?: unknown): void;
};

export const logger: Logger = {
  debug: (event, fields = {}) => emit('debug', event, fields),
  info: (event, fields = {}) => emit('info', event, fields),
  warn: (event, fields = {}, err?: unknown) => emit('warn', event, fields, err),
  error: (event, fields = {}, err?: unknown) => emit('error', event, fields, err),
};
