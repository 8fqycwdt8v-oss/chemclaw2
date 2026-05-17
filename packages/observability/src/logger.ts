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

function safeStringify(record: LogFields): string {
  // JSON.stringify throws on circular refs (e.g. an Error.cause chain that
  // refers back to its parent). The logger sits in error-handling code, so a
  // throw here would mask the original failure — fall back to a minimal line.
  try {
    return JSON.stringify(record);
  } catch {
    return JSON.stringify({ level: record.level, time: record.time, event: record.event, log_serialize_failed: true });
  }
}

function emit(level: LogLevel, event: string, fields: LogFields, err?: unknown): void {
  if (LEVEL_PRIORITY[level] < minLevel()) return;

  // Belt-and-braces: the logger is called from catch blocks. Any exception
  // here would replace the original error and confuse the caller. Swallow
  // everything past this point — losing one log line is preferable to losing
  // the real error.
  try {
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
    const line = safeStringify(record);
    if (level === 'error' || level === 'warn') {
      process.stderr.write(line + '\n');
    } else {
      process.stdout.write(line + '\n');
    }
  } catch {
    // intentionally empty — never let logging crash a caller
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
