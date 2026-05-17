import { logger } from '@chemclaw2/observability';
import { CONTROLLED_SUBSTANCE_NAMES, normalizeForGate } from './scheduled-substance-gate';

// Wave-3h security fix: also strip zero-width characters before SSN matching
// so a prompt like "123​-45-6789" can't bypass the block. NFKC happens
// inside normalizeForGate.

// Pattern matches US Social Security Numbers (NNN-NN-NNNN).
// CAS numbers (NN...-NN-N) do not match — they end in a single digit.
const SSN_RE = /\b\d{3}-\d{2}-\d{4}\b/;
const SSN_RE_GLOBAL = /\b\d{3}-\d{2}-\d{4}\b/g;

// Additional secret / credential patterns for tool inputs. The set is
// intentionally narrow — false positives are costly here because redaction
// runs on every tool call. SSN stays as the canonical sensitive-PII case;
// the rest are credential shapes that users sometimes paste into prompts.
const SECRET_PATTERNS: Array<[RegExp, string, string]> = [
  // Anthropic / OpenAI / Stripe-style secret keys.
  [/\b(sk|rk|pk)[-_][A-Za-z0-9]{20,}\b/g, '[REDACTED-API-KEY]', 'api_key'],
  // OAuth / generic bearer tokens.
  [/\b[Bb]earer\s+[A-Za-z0-9._~+/=-]{16,}\b/g, 'Bearer [REDACTED]', 'bearer_token'],
  // AWS access keys.
  [/\bAKIA[0-9A-Z]{16}\b/g, '[REDACTED-AWS-KEY]', 'aws_access_key'],
  // GitHub personal-access tokens (classic + fine-grained).
  [/\bghp_[A-Za-z0-9]{30,}\b/g, '[REDACTED-GITHUB-TOKEN]', 'github_pat'],
  [/\bgithub_pat_[A-Za-z0-9_]{30,}\b/g, '[REDACTED-GITHUB-TOKEN]', 'github_pat'],
];

function redactSecrets(input: string, toolName: string): { redacted: string; matched: boolean } {
  let out = input;
  let matched = false;
  for (const [re, sub, kind] of SECRET_PATTERNS) {
    const count = (out.match(re) ?? []).length;
    if (count > 0) {
      logger.warn('credential_redacted', { tool: toolName, kind, count });
      out = out.replace(re, sub);
      matched = true;
    }
  }
  return { redacted: out, matched };
}

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
  toolName: string,
  toolInput: Record<string, unknown>,
): { action: 'allow'; input?: Record<string, unknown> } | { action: 'block'; reason: string } {
  // Collect all string leaf values from the input object
  const stringValues = extractStringValues(toolInput);

  // Block if any string value (not key/URL path) contains a controlled substance name.
  // Apply the same normalization used in scheduledSubstanceGate for consistency.
  for (const val of stringValues) {
    if (CONTROLLED_SUBSTANCE_NAMES.test(normalizeForGate(val))) {
      logger.warn('tool_input_block_controlled_substance', { tool: toolName });
      return {
        action: 'block',
        reason: 'Tool input blocked: contains a term that is not permitted in this context.',
      };
    }
  }

  // Redact PII (SSN) and common credential shapes from all string values.
  // Operates on the serialized JSON so depth doesn't matter.
  const inputStr = JSON.stringify(toolInput);
  let working = inputStr;
  let didRedact = false;

  const ssnMatchCount = (working.match(SSN_RE_GLOBAL) ?? []).length;
  if (ssnMatchCount > 0) {
    logger.warn('pii_redacted', { tool: toolName, kind: 'ssn', count: ssnMatchCount });
    working = working.replace(SSN_RE_GLOBAL, '[REDACTED-SSN]');
    didRedact = true;
  }

  const { redacted, matched } = redactSecrets(working, toolName);
  if (matched) {
    working = redacted;
    didRedact = true;
  }

  if (didRedact) {
    return {
      action: 'allow',
      input: JSON.parse(working) as Record<string, unknown>,
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
  // NFKC-normalize + zero-width-strip before pattern match so bypass attempts
  // like "123​-45-6789" or NFKC-equivalent variants get caught the same as
  // the canonical form. SSN_RE is non-global so .test() is stateless.
  if (SSN_RE.test(normalizeForGate(prompt))) {
    logger.warn('prompt_block', { reason: 'ssn', prompt_len: prompt.length });
    return {
      action: 'block',
      reason: 'Prompt blocked: contains what looks like a Social Security Number. Remove the SSN and resubmit.',
    };
  }
  return { action: 'allow' };
}
