// Shared slug regex / validator. Was duplicated across 4 files (#18).
// agent-tools is the lowest-level package both apps/web and the workers
// depend on, so it's the right home.
export const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
export const SLUG_MAX_LEN = 200;
export const RESERVED_SLUGS = new Set(['new', 'index', '_new']);

export function isValidSlug(slug: string): boolean {
  return SLUG_RE.test(slug) && slug.length <= SLUG_MAX_LEN && !RESERVED_SLUGS.has(slug);
}
