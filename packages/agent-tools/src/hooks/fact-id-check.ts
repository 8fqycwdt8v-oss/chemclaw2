import { db } from '@chemclaw2/db';
import { compounds } from '@chemclaw2/db';
import { inArray } from 'drizzle-orm';

const CAS_RE = /\b\d{2,10}-\d{2}-\d\b/g;

export async function checkToolOutput(
  _toolName: string,
  toolOutput: string,
): Promise<{ warnings: string[] }> {
  const casNumbers = [...new Set(toolOutput.match(CAS_RE) ?? [])];
  if (casNumbers.length === 0) return { warnings: [] };

  // Batch query — single IN(...) instead of N individual SELECTs
  const found = await db
    .select({ casNumber: compounds.casNumber })
    .from(compounds)
    .where(inArray(compounds.casNumber, casNumbers));

  const foundSet = new Set(found.map((r) => r.casNumber));
  const warnings = casNumbers
    .filter((cas) => !foundSet.has(cas))
    .map((cas) => `CAS ${cas} found in tool output is not in the compound registry — verify accuracy`);

  return { warnings };
}
