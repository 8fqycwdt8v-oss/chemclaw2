import { db } from '@chemclaw2/db';
import { compounds } from '@chemclaw2/db';
import { inArray } from 'drizzle-orm';
import { trace } from '@opentelemetry/api';

const CAS_RE = /\b\d{2,10}-\d{2}-\d\b/g;

export async function checkToolOutput(
  toolName: string,
  toolOutput: string,
): Promise<{ warnings: string[] }> {
  const casNumbers = [...new Set(toolOutput.match(CAS_RE) ?? [])];
  if (casNumbers.length === 0) return { warnings: [] };

  // Batch query — single IN(...) instead of N individual SELECTs.
  // Fail open on DB errors: a compliance flag should not break the agent response.
  let found: Array<{ casNumber: string | null }>;
  try {
    found = await db
      .select({ casNumber: compounds.casNumber })
      .from(compounds)
      .where(inArray(compounds.casNumber, casNumbers));
  } catch {
    return { warnings: [] };
  }

  const foundSet = new Set(found.map((r) => r.casNumber));
  const unverified = casNumbers.filter((cas) => !foundSet.has(cas));

  if (unverified.length > 0) {
    // Record unverified CAS numbers as a span event so they appear in Langfuse traces
    // without blocking the agent response. Does not throw — this is a compliance flag only.
    const span = trace.getActiveSpan();
    if (span) {
      span.addEvent('unverified_cas_numbers', {
        tool_name: toolName,
        cas_numbers: unverified.join(','),
        count: unverified.length,
      });
    }
  }

  const warnings = unverified.map(
    (cas) => `CAS ${cas} found in tool output is not in the compound registry — verify accuracy`,
  );

  return { warnings };
}
