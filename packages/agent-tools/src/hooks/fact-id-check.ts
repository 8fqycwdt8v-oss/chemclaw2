import { db } from '@chemclaw2/db';
import { compounds } from '@chemclaw2/db';
import { sql } from 'drizzle-orm';

const CAS_RE = /\b\d{2,7}-\d{2}-\d\b/g;

export async function checkToolOutput(
  _toolName: string,
  toolOutput: string,
): Promise<{ warnings: string[] }> {
  const casNumbers = [...new Set(toolOutput.match(CAS_RE) ?? [])];
  if (casNumbers.length === 0) return { warnings: [] };

  const warnings: string[] = [];
  for (const cas of casNumbers) {
    const [row] = await db
      .select({ id: compounds.id })
      .from(compounds)
      .where(sql`cas_number = ${cas}`)
      .limit(1);
    if (!row) {
      warnings.push(`CAS ${cas} found in tool output is not in the compound registry — verify accuracy`);
    }
  }
  return { warnings };
}
