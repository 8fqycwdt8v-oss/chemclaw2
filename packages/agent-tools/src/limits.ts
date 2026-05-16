// Centralized byte/length caps for agent + route boundaries. Prefer importing
// from here over hard-coding numbers so cross-module limits stay aligned.

export const MAX_SMILES_LEN = 10_000;
export const MAX_REACTION_SMILES_LEN = 20_000;
export const MAX_MARKDOWN_LEN = 500_000;
export const MAX_BODY_BYTES = 32_768;
export const MAX_PROMPT_BYTES = 32_768;
export const MAX_TITLE_LEN = 500;
export const MAX_SLUG_LEN = 200;
export const MAX_PROJECT_LEN = 100;
export const MAX_RATIONALE_LEN = 2000;
export const MAX_CITATIONS = 200;
export const MAX_PROPERTY_BATCH = 100;
export const MAX_PROJECT_KEY_LEN = 64;
export const PROJECT_KEY_RE = /^[A-Za-z0-9:_-]{1,64}$/;
