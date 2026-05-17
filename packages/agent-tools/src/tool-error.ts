import { trace } from '@opentelemetry/api';

/**
 * Convert any thrown error into a sanitized tool result + an OTel event the
 * trace pipeline (Langfuse) can surface. Use in agent tool execute() catch
 * arms instead of returning a bare `"...failed"` string.
 */
export function toolError(toolName: string, err: unknown): { error: string } {
  const message = err instanceof Error ? err.message : String(err);
  trace.getActiveSpan()?.addEvent('tool.execute_failed', {
    tool: toolName,
    message: message.slice(0, 500),
  });
  return { error: message || `${toolName} failed` };
}
