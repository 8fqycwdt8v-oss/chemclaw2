import { knownCasNumbers } from '@chemclaw2/db';
import { trace } from '@opentelemetry/api';

// CAS numbers: 2-7 digits, hyphen, 2 digits, hyphen, 1 check digit.
// The pre-v1.6.1 range of 2-10 false-matched ISO dates like 2024-01-05.
const CAS_RE = /\b\d{2,7}-\d{2}-\d\b/g;

export async function checkToolOutput(
  toolName: string,
  toolOutput: string,
): Promise<{ warnings: string[] }> {
  const casNumbers = [...new Set(toolOutput.match(CAS_RE) ?? [])];
  if (casNumbers.length === 0) return { warnings: [] };

  // Fail open on DB errors: a compliance flag should not break the agent response.
  let known: Set<string | null>;
  try {
    known = await knownCasNumbers(casNumbers);
  } catch (err) {
    trace.getActiveSpan()?.addEvent('fact_id_check_db_error', {
      tool_name: toolName,
      cas_count: casNumbers.length,
      message: err instanceof Error ? err.message : String(err),
    });
    console.warn(
      '[fact-id-check] DB query failed — compliance check skipped:',
      err instanceof Error ? err.message : err,
    );
    return { warnings: [] };
  }

  const unverified = casNumbers.filter((c) => !known.has(c));
  if (unverified.length > 0) {
    trace.getActiveSpan()?.addEvent('unverified_cas_numbers', {
      tool_name: toolName,
      cas_numbers: unverified.join(','),
      count: unverified.length,
    });
  }
  return {
    warnings: unverified.map((c) =>
      `CAS ${c} found in tool output is not in the compound registry — verify accuracy`,
    ),
  };
}
