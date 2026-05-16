export const SLUG_RE = /^[a-z0-9]+(?:-[a-z0-9]+)*$/;
export const SLUG_MAX_LEN = 200;

export function isValidSlug(slug: string): boolean {
  return SLUG_RE.test(slug) && slug.length <= SLUG_MAX_LEN;
}
