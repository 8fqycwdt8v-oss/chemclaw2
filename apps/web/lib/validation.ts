// Re-export from the canonical home in @chemclaw2/agent-tools so route
// handlers can continue importing from `@/lib/validation`.
export { SLUG_RE, SLUG_MAX_LEN, RESERVED_SLUGS, isValidSlug } from '@chemclaw2/agent-tools';
export { UUID_RE, isUuid } from '@chemclaw2/agent-tools';
export { isValidTiptapDoc } from '@chemclaw2/agent-tools';

import { MAX_CITATIONS } from '@chemclaw2/agent-tools';

const MAX_CITATION_FIELD_LEN = 1_000;

export function validateCitations(value: unknown): string | null {
  if (!Array.isArray(value)) return null;
  if (value.length > MAX_CITATIONS) return 'too many citations';
  for (const c of value) {
    if (
      typeof c?.citationId !== 'string' || c.citationId.length > MAX_CITATION_FIELD_LEN ||
      typeof c?.sourceType !== 'string' || c.sourceType.length > MAX_CITATION_FIELD_LEN ||
      typeof c?.label !== 'string' || c.label.length > MAX_CITATION_FIELD_LEN ||
      (c.sourceId !== undefined && (typeof c.sourceId !== 'string' || c.sourceId.length > MAX_CITATION_FIELD_LEN))
    ) {
      return 'invalid citation fields';
    }
  }
  return null;
}
