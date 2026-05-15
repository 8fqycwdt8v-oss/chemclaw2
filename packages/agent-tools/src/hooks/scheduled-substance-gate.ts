// Shared list of controlled/scheduled substance names — kept in sync with redaction.ts
export const CONTROLLED_SUBSTANCE_NAMES =
  /\b(fentanyl|carfentanil|methamphetamine|meth|heroin|mdma|nitazene|acetylfentanyl|furanylfentanyl)\b/i;

const SYNTHESIS_VERBS =
  /\b(synthesize|synthesis|manufacture|produce|make|cook|prepare|recipe|route)\b/i;

/**
 * Returns blocked=true when the prompt contains both a controlled substance
 * name AND a synthesis-intent verb. Using two independent regexes prevents
 * trivial bypass via whitespace or separators between terms.
 *
 * The block reason shown to the client is intentionally generic — the matched
 * term is NOT echoed to avoid telling the requester what to rephrase.
 */
export function scheduledSubstanceGate(prompt: string): { blocked: boolean; reason?: string } {
  if (CONTROLLED_SUBSTANCE_NAMES.test(prompt) && SYNTHESIS_VERBS.test(prompt)) {
    return {
      blocked: true,
      reason: 'Request blocked: synthesis instructions for scheduled/controlled substances are not permitted.',
    };
  }
  return { blocked: false };
}
