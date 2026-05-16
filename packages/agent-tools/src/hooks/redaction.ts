import { CONTROLLED_SUBSTANCE_NAMES, normalizeForGate } from './scheduled-substance-gate';

// Pattern matches US Social Security Numbers (NNN-NN-NNNN).
// Not a general PII scanner — only SSNs. CAS numbers (NN...-NN-N) do not match.
const SSN_RE = /\b\d{3}-\d{2}-\d{4}\b/g;

/**
 * Check a tool input object for controlled substance names (block) or SSN patterns (redact).
 *
 * Substance matching is done on individual string values after NFKC normalization
 * and zero-width stripping — consistent with scheduledSubstanceGate.
 *
 * SSN redaction operates on the serialized JSON so it catches values regardless
 * of nesting depth; the regex uses \b word-boundary anchors so it does not match
 * CAS numbers (which end in a single digit, giving NN...-NN-N format).
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

  // Block if any string value (not key/URL path) contains a controlled substance name.
  // Apply the same normalization used in scheduledSubstanceGate for consistency.
  for (const val of stringValues) {
    if (CONTROLLED_SUBSTANCE_NAMES.test(normalizeForGate(val))) {
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

/** Recursively extract string leaf values, skipping well-formed URL strings to
 *  avoid false-positives on legitimate scientific references (e.g. PubChem URLs).
 *  Strings starting with http/https but containing whitespace are NOT skipped —
 *  they are not valid URLs and could be injection attempts like
 *  "https://fake.com/fentanyl synthesis". */
function extractStringValues(obj: unknown): string[] {
  if (typeof obj === 'string') {
    const isUrl = (obj.startsWith('http://') || obj.startsWith('https://')) && !/\s/.test(obj);
    return isUrl ? [] : [obj];
  }
  if (Array.isArray(obj)) return obj.flatMap(extractStringValues);
  if (obj !== null && typeof obj === 'object') {
    return Object.values(obj as Record<string, unknown>).flatMap(extractStringValues);
  }
  return [];
}

/**
 * Wave-3a A5: redact / block the user's free-text prompt before it reaches
 * the model. Mirrors `checkToolInput` semantics but applies to the raw
 * prompt string.
 *
 * Why block rather than silent-redact: the SDK's UserPromptSubmit hook can
 * only return `additionalContext` or suppress the prompt entirely; it can't
 * mutate the prompt the model sees. A silent-redact would leave the model
 * looking at the original PII string, defeating the purpose. Blocking with
 * a clear message lets the user resubmit a cleaned version.
 *
 * Controlled-substance terms hard-block via the scheduledSubstanceGate
 * upstream of the chat route (apps/web/app/api/chat/route.ts). This helper
 * stays focused on the redaction case — SSN-like patterns — so we don't
 * double-fire the substance gate.
 */
export function checkUserPrompt(
  prompt: string,
): { action: 'allow' } | { action: 'block'; reason: string } {
  if (SSN_RE.test(prompt)) {
    // Reset lastIndex; SSN_RE is a /g regex and stateful across .test calls.
    SSN_RE.lastIndex = 0;
    return {
      action: 'block',
      reason: 'Prompt blocked: contains what looks like a Social Security Number. Remove the SSN and resubmit.',
    };
  }
  SSN_RE.lastIndex = 0;
  return { action: 'allow' };
}
