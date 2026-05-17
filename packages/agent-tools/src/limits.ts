// Shared byte/length caps for agent + route boundaries.

export const MAX_MARKDOWN_LEN = 500_000;
export const MAX_PROMPT_BYTES = 32_768;
export const MAX_TITLE_LEN = 500;
export const MAX_PROJECT_LEN = 100;
export const MAX_RATIONALE_LEN = 2000;
export const MAX_CITATIONS = 200;
export const PROJECT_KEY_RE = /^[A-Za-z0-9:_-]{1,64}$/;
