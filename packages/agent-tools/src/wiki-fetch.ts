import { z } from 'zod';
import { getWikiPage, searchWikiByFTS, semanticSearchWiki } from '@chemclaw2/db';
import { isValidSlug } from './slug';
import type { ToolDef } from './tool-def';

const SLUG_PREVIEW_CHARS = 2000;

type EmbedFn = (text: string) => Promise<number[]>;

type LookupInput = {
  slug?: string;
  query?: string;
  semantic?: boolean;
  full?: boolean;
};

async function executeWikiLookup(input: LookupInput, embedFn?: EmbedFn) {
  if (input.slug) {
    if (!isValidSlug(input.slug)) {
      return { error: 'Invalid slug format' };
    }
    const page = await getWikiPage(input.slug);
    if (!page) return { found: false };
    const text = page.contentText;
    const truncated = !input.full && text.length > SLUG_PREVIEW_CHARS;
    return {
      found: true,
      slug: page.slug,
      title: page.title,
      text: truncated ? text.slice(0, SLUG_PREVIEW_CHARS) : text,
      truncated,
      version: page.version,
      maturity: page.maturity,
      needsReview: page.needsReview,
      archived: page.archived,
    };
  }
  if (input.query) {
    if (input.query.length > 500) return { error: 'Query too long (max 500 chars)' };
    if (input.semantic && embedFn) {
      const embedding = await embedFn(input.query);
      const results = await semanticSearchWiki(embedding, 5);
      return { mode: 'semantic', results };
    }
    const results = await searchWikiByFTS(input.query, 5);
    return {
      mode: 'fts',
      results: results.map((r) => ({
        slug: r.slug,
        title: r.title,
        maturity: r.maturity,
        excerpt: r.contentText.slice(0, 300),
      })),
    };
  }
  return { error: 'Provide either slug or query' };
}

const wikiFetchSchema = {
  slug: z.string().optional().describe('Direct page slug (e.g. "aspirin")'),
  query: z.string().optional().describe('Full-text or semantic search query'),
  semantic: z.boolean().optional().describe('Use vector similarity search (requires query)'),
  full: z.boolean().optional().describe(
    'When using slug, return full content_text instead of a 2000-char preview',
  ),
};

/** Plain execute-only version (no SDK dependency) */
export const wikiFetchTool: ToolDef<typeof wikiFetchSchema> = {
  name: 'wiki_lookup',
  description:
    'Look up or search the organization wiki. Provide slug for direct lookup, query for full-text ' +
    'search, or query+semantic=true for vector similarity search. Archived pages are excluded; ' +
    'maturity is surfaced so you can disclaim exploratory content.',
  schema: wikiFetchSchema,
  execute: (input) => executeWikiLookup(input),
};

/** Factory that returns a wiki tool wired with an embed function for semantic search. */
export function createWikiFetchTool(embedFn: EmbedFn): ToolDef<typeof wikiFetchSchema> {
  return {
    ...wikiFetchTool,
    execute: (input) => executeWikiLookup(input, embedFn),
  };
}
