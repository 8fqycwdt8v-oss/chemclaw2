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

const URL_LIKE_TYPES = new Set(['url', 'http', 'https', 'web', 'link']);

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
 *   - every `[N]` marker referenced in the body has a matching citation entry
 *   - every URL-type citation points to an allowed science domain
 *   - no two citations share the same citationId (collisions break disambiguation)
 *
 * Returns the first failure (agents are easier to debug with a single concrete
 * error than a list of issues). Returns `{ok: true}` if nothing is wrong.
 *
 * Note: this is intentionally strict on URL citations because they bypass the
 * doc-fetch allowlist — a rendered citation pill is a clickable outbound link.
 */
export function validateCitations(
  body: string,
  citations: CitationInput[],
): CitationValidationResult {
  const ids = new Set<string>();
  for (const c of citations) {
    if (ids.has(c.citationId)) {
      return { ok: false, reason: `duplicate citationId "${c.citationId}"` };
    }
    ids.add(c.citationId);

    if (URL_LIKE_TYPES.has(c.sourceType.toLowerCase())) {
      const url = c.sourceId ?? c.label;
      if (!isAllowedCitationUrl(url)) {
        return {
          ok: false,
          reason: `citation "${c.citationId}" points to "${url}" which is not on the allowed science-domain list`,
        };
      }
    }
  }

  // Collect [N] markers from the body. Match plain numeric markers; ignore
  // [text] forms that may be markdown links.
  const referenced = new Set<string>();
  const re = /\[([^\]\s]+)\]/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(body)) !== null) {
    const inner = m[1];
    // Pure numeric markers like [1], [12] — also short alphanumeric like [a]
    if (/^[A-Za-z0-9_-]{1,20}$/.test(inner)) {
      referenced.add(inner);
    }
  }
  for (const marker of referenced) {
    if (!ids.has(marker)) {
      return {
        ok: false,
        reason: `body references [${marker}] but no citation with citationId "${marker}" was provided`,
      };
    }
  }

  return { ok: true };
}
