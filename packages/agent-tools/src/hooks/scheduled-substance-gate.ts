// Shared list of controlled/scheduled substance names — kept in sync with redaction.ts
export const CONTROLLED_SUBSTANCE_NAMES =
  /\b(fentanyl|carfentanil|acetylfentanyl|furanylfentanyl|nitazene|metonitazene|isotonitazene|protonitazene|methamphetamine|meth|amphetamine|heroin|mdma|ecstasy|molly|cocaine|crack|lsd|psilocybin|psilocin|dmt|mescaline|peyote|ghb|pcp|phencyclidine|ketamine|oxycodone|hydrocodone|hydromorphone|oxymorphone|morphine|codeine|buprenorphine|tramadol|tapentadol|mdpv|cathinone|mephedrone|ephedrine|pseudoephedrine|safrole)\b/i;

const SYNTHESIS_VERBS =
  /\b(synthesize|synthesise|synthesis|manufacture|produce|make|cook|prepare|recipe|route)\b/i;

// Zero-width and soft-hyphen characters commonly used to evade keyword filters
const ZERO_WIDTH_RE = /[­​-‍⁠﻿]/g;

function normalizeForGate(s: string): string {
  return s.normalize('NFKC').replace(ZERO_WIDTH_RE, '');
}

/**
 * Returns blocked=true when the prompt contains both a controlled substance
 * name AND a synthesis-intent verb. Two independent regexes prevent trivial
 * bypass via whitespace or separators between terms.
 *
 * Input is NFKC-normalized and zero-width characters stripped before matching
 * to resist Unicode homoglyph and invisible-character bypass attempts.
 *
 * The block reason shown to the client is intentionally generic — the matched
 * term is NOT echoed to avoid telling the requester what to rephrase.
 *
 * Limitation: only the current-turn prompt is inspected. A multi-turn split
 * attack (substance in a prior message, synthesis verb in the current one)
 * is not caught here. See BACKLOG for the architectural mitigation.
 */
export function scheduledSubstanceGate(prompt: string): { blocked: boolean; reason?: string; matched: boolean } {
  const normalized = normalizeForGate(prompt);
  const matched = CONTROLLED_SUBSTANCE_NAMES.test(normalized) && SYNTHESIS_VERBS.test(normalized);
  if (matched) {
    return {
      blocked: true,
      matched: true,
      reason: 'Request blocked: synthesis instructions for scheduled/controlled substances are not permitted.',
    };
  }
  return { blocked: false, matched: false };
}

export { normalizeForGate };
