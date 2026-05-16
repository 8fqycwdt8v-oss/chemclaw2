import { spawn, type ChildProcess } from 'child_process';
import { trace } from '@opentelemetry/api';

const PYTHON = process.env.MCP_PYTHON_PATH ?? '/opt/venv/bin/python';
const DEFAULT_TIMEOUT_MS = 20_000;
const INIT_ID = 1;
const TOOL_CALL_ID = 2;

export type CallMcpOptions = {
  timeoutMs?: number;
  /** Set of currently-running child procs to register so SIGTERM can kill them. */
  activeProcs?: Set<ChildProcess>;
};

/**
 * Single MCP stdio call: initialize → notifications/initialized → tools/call.
 * Newline-delimited JSON-RPC 2.0. Used by both the per-request /api/fingerprint
 * route and the fp-worker batch handlers — keep the contract in one place.
 */
export function callMcpTool(
  pythonModule: string,
  toolName: string,
  args: Record<string, unknown>,
  opts: CallMcpOptions = {},
): Promise<Record<string, unknown>> {
  const timeoutMs = opts.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  return new Promise((resolve, reject) => {
    const proc = spawn(PYTHON, ['-m', pythonModule], { stdio: ['pipe', 'pipe', 'inherit'] });
    opts.activeProcs?.add(proc);

    let buf = '';
    let initDone = false;
    let settled = false;

    const settle = (fn: () => void) => {
      if (settled) return;
      settled = true;
      opts.activeProcs?.delete(proc);
      clearTimeout(timer);
      fn();
    };
    const timer = setTimeout(() => {
      proc.kill();
      settle(() => reject(new Error(`MCP tool ${toolName} timed out after ${timeoutMs}ms`)));
    }, timeoutMs);
    const send = (msg: object) => proc.stdin.write(JSON.stringify(msg) + '\n');

    send({
      jsonrpc: '2.0',
      id: INIT_ID,
      method: 'initialize',
      params: { protocolVersion: '2024-11-05', capabilities: {}, clientInfo: { name: 'chemclaw2', version: '1.0' } },
    });

    proc.stdout.on('data', (chunk: Buffer) => {
      buf += chunk.toString();
      const lines = buf.split('\n');
      buf = lines.pop() ?? '';
      for (const line of lines) {
        if (!line.trim()) continue;
        let msg: Record<string, unknown>;
        try {
          msg = JSON.parse(line) as Record<string, unknown>;
        } catch {
          trace.getActiveSpan()?.addEvent('mcp_response_line_unparseable', {
            tool: toolName,
            sample: line.slice(0, 200),
          });
          continue;
        }
        if (!initDone && (msg as { id?: number }).id === INIT_ID) {
          initDone = true;
          send({ jsonrpc: '2.0', method: 'notifications/initialized', params: {} });
          send({ jsonrpc: '2.0', id: TOOL_CALL_ID, method: 'tools/call', params: { name: toolName, arguments: args } });
        } else if ((msg as { id?: number }).id === TOOL_CALL_ID) {
          proc.stdin.end();
          const err = (msg as { error?: { message?: string } }).error;
          if (err) {
            settle(() => reject(new Error(err.message ?? 'MCP tool error')));
            return;
          }
          const result = msg as { result?: { content?: Array<{ text?: string }> } };
          const text = result.result?.content?.[0]?.text;
          try {
            const parsed = text ? (JSON.parse(text) as Record<string, unknown>) : ((msg.result as Record<string, unknown>) ?? {});
            settle(() => resolve(parsed));
          } catch {
            settle(() => reject(new Error(`Failed to parse MCP response for ${toolName}`)));
          }
        }
      }
    });
    proc.on('error', (e) => settle(() => reject(e)));
    proc.on('close', (code) => {
      if (code !== 0) settle(() => reject(new Error(`MCP ${pythonModule} exited with code ${code}`)));
      else settle(() => reject(new Error(`MCP ${pythonModule} closed before tool response`)));
    });
  });
}
