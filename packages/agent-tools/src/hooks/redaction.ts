import { CONTROLLED_SUBSTANCE_NAMES } from './scheduled-substance-gate';

// Pattern matches US Social Security Numbers (NNN-NN-NNNN).
// Not a general PII scanner — only SSNs. CAS numbers (NN...-NN-N) do not match.
const SSN_RE = /\b\d{3}-\d{2}-\d{4}\b/g;

/**
 * Check a tool input object for controlled substance names (block) or SSN patterns (redact).
 *
 * Matching is done on individual string values rather than the serialized JSON
 * to avoid false positives from URL paths or field names that happen to contain
 * substance names in a non-synthesis context.
 *
 * The block reason shown to the client is intentionally generic — the matched
 * term is NOT echoed to avoid confirming what triggered the block.
 */
export function checkToolInput(
  _toolName: string,
  toolInput: Record<string, unknown>,
): { action: 'allow'; input?: Record<string, unknown> } | { action: 'block'; reason: string } {
  // Collect all string leaf values from the input object
  const stringValues = extractStringValues(toolInput);

  // Block if any string value (not key/URL path) contains a controlled substance name
  for (const val of stringValues) {
    if (CONTROLLED_SUBSTANCE_NAMES.test(val)) {
      return {
        action: 'block',
        reason: 'Tool input blocked: contains a term that is not permitted in this context.',
      };
    }
  }

  // Redact SSN patterns from all string values
  const inputStr = JSON.stringify(toolInput);
  const sanitized = inputStr.replace(SSN_RE, '[REDACTED-SSN]');
  if (sanitized !== inputStr) {
    return {
      action: 'allow',
      input: JSON.parse(sanitized) as Record<string, unknown>,
    };
  }

  return { action: 'allow' };
}

/** Recursively extract all string leaf values from a nested object. */
function extractStringValues(obj: unknown): string[] {
  if (typeof obj === 'string') return [obj];
  if (Array.isArray(obj)) return obj.flatMap(extractStringValues);
  if (obj !== null && typeof obj === 'object') {
    return Object.values(obj as Record<string, unknown>).flatMap(extractStringValues);
  }
  return [];
}
