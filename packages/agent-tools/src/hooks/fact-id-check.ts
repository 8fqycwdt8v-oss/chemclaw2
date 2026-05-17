import { knownCasNumbers } from '@chemclaw2/db';
import { logger } from '@chemclaw2/observability';

// Suppress duplicate fact_id_check fail-open events during a DB outage.
// We still keep the running count so a final summary can be emitted.
let consecutiveFailures = 0;
const STORM_THRESHOLD = 10;

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
    if (consecutiveFailures > 0) {
      logger.info('fact_id_check_db_recovered', { prior_failures: consecutiveFailures });
      consecutiveFailures = 0;
    }
  } catch (err) {
    consecutiveFailures++;
    // Suppress duplicate logs when the DB is down — emit only the first N
    // and one summary line after the threshold to avoid flooding the pipeline.
    if (consecutiveFailures <= STORM_THRESHOLD) {
      logger.warn(
        'fact_id_check_db_error',
        { tool: toolName, cas_count: casNumbers.length, consecutive_failures: consecutiveFailures },
        err,
      );
    } else if (consecutiveFailures === STORM_THRESHOLD + 1) {
      logger.error('fact_id_check_storm', { threshold: STORM_THRESHOLD });
    }
    return { warnings: [] };
  }

  const unverified = casNumbers.filter((c) => !known.has(c));
  if (unverified.length > 0) {
    logger.info('unverified_cas_numbers', {
      tool: toolName,
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
