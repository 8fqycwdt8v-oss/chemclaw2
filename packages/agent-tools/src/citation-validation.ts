import { logger } from '@chemclaw2/observability';
import { ALLOWED_DOMAINS } from './doc-fetch';

export type CitationInput = {
  citationId: string;
  sourceType: string;
  sourceId?: string;
  label: string;
};

export type CitationValidationResult =
  | { ok: true }
  | { ok: false; reason: string };

// A citation marker in the body is `[N]` where N is purely digits.
// Numeric-only deliberately rules out:
//   - SMILES brackets: [nH], [CH3], [H], [Cl], [NH4], [C@H], ...
//   - Markdown links: [paper](url)
//   - Generic bracketed text: [fig 1], [note]
// Agents that use non-numeric ids (e.g. [a], [REF-2024]) lose the consistency
// check but no longer get blocked by chemistry-content false positives.
const BODY_MARKER_RE = /\[(\d{1,4})\]/g;

function looksLikeHttpUrl(s: string | undefined): s is string {
  if (typeof s !== 'string') return false;
  return s.startsWith('http://') || s.startsWith('https://');
}

function isAllowedCitationUrl(raw: string): boolean {
  let u: URL;
  try {
    u = new URL(raw);
  } catch {
    return false;
  }
  if (u.protocol !== 'http:' && u.protocol !== 'https:') return false;
  const host = u.hostname.toLowerCase();
  return ALLOWED_DOMAINS.some((d) => host === d || host.endsWith(`.${d}`));
}

/**
 * Validate that:
 *   - every numeric `[N]` marker in the body has a matching citation entry
 *   - any citation whose sourceId is an http(s) URL points to an allowed
 *     science domain — the sourceType label is not trusted because an agent
 *     can pick any string ("doi", "paper", "pdf", ...) and bypass the guard
 *   - no two citations share the same citationId
 *
 * Returns the first failure. Returns `{ok: true}` if nothing is wrong.
 */
export function validateCitations(
  body: string,
  citations: CitationInput[],
): CitationValidationResult {
  const ids = new Set<string>();
  for (const c of citations) {
    if (ids.has(c.citationId)) {
      logger.warn('citation_validation_failed', { kind: 'duplicate_id', citation_id: c.citationId });
      return { ok: false, reason: `duplicate citationId "${c.citationId}"` };
    }
    ids.add(c.citationId);

    // Allowlist check is gated on the VALUE looking like an http URL, not on
    // the sourceType string. The label field may also carry a URL.
    for (const candidate of [c.sourceId, c.label]) {
      if (looksLikeHttpUrl(candidate) && !isAllowedCitationUrl(candidate)) {
        logger.warn('citation_validation_failed', { kind: 'disallowed_url', citation_id: c.citationId });
        return {
          ok: false,
          reason: `citation "${c.citationId}" points to "${candidate}" which is not on the allowed science-domain list`,
        };
      }
    }
  }

  const referenced = new Set<string>();
  let m: RegExpExecArray | null;
  // Reset lastIndex defensively (BODY_MARKER_RE is module-level + /g).
  BODY_MARKER_RE.lastIndex = 0;
  while ((m = BODY_MARKER_RE.exec(body)) !== null) {
    referenced.add(m[1]);
  }
  for (const marker of referenced) {
    if (!ids.has(marker)) {
      logger.warn('citation_validation_failed', { kind: 'missing_marker', marker });
      return {
        ok: false,
        reason: `body references [${marker}] but no citation with citationId "${marker}" was provided`,
      };
    }
  }

  return { ok: true };
}
