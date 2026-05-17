import { z } from 'zod';
import { upsertWikiPage } from '@chemclaw2/db';
import { logger } from '@chemclaw2/observability';
import { isValidSlug } from './slug';
import { markdownToTiptap } from './markdown-to-tiptap';
import { validateCitations } from './citation-validation';
import { isValidTiptapDoc } from './tiptap';
import { toolError } from './tool-error';
import { MAX_TITLE_LEN, MAX_MARKDOWN_LEN, MAX_PROJECT_LEN } from './limits';
import type { ToolDef } from './tool-def';

const wikiUpsertSchema = {
  slug: z.string().describe('Lowercase kebab-case page slug'),
  title: z.string().describe('Human-readable title'),
  content_text: z.string().describe(
    'Markdown body (gets parsed into Tiptap, then chunked + embedded)',
  ),
  project: z.string().optional().describe(
    'Optional project tag for scoping (e.g. "project-x")',
  ),
  citations: z.array(z.object({
    citationId: z.string(),
    sourceType: z.string().describe('e.g. "compound", "reaction", "url", "doc"'),
    sourceId: z.string().optional(),
    label: z.string(),
  })).optional().describe('Sources used (compounds, reactions, URLs, doc IDs)'),
};

/**
 * Factory: lets the agent write or update a wiki page on the user's behalf.
 * userId is captured at factory time so the LLM cannot impersonate someone else.
 *
 * Agent-authored pages are flagged needs_review=true by default — chemists
 * see them in the review queue before promoting to validated/authoritative.
 *
 * The body is parsed as markdown into proper Tiptap JSON so headers, lists,
 * bold/italic, and inline code render in the editor instead of leaking raw
 * markup. content_text (raw markdown) remains the source for FTS + embeddings.
 */
export function createWikiUpsertTool(
  userId: string,
  embedFn: (texts: string[]) => Promise<number[][]>,
): ToolDef<typeof wikiUpsertSchema> {
  return {
    name: 'wiki_upsert',
    description:
      'Create or update a wiki page. Use after compiling a synthesis review, ' +
      'campaign summary, or analytical interpretation worth keeping. The page ' +
      'is versioned automatically; citations should reference compounds, ' +
      'reactions, documents, or external URLs. Slug must be lowercase ' +
      'kebab-case (e.g. "aspirin-synthesis"). Body is parsed as markdown; ' +
      'all [N] markers in the body must have a matching citation entry, and ' +
      'URL citations must point to an allowed science domain.',
    schema: wikiUpsertSchema,
    async execute(input) {
      if (!isValidSlug(input.slug)) {
        return { error: 'slug must be lowercase kebab-case, ≤200 chars' };
      }
      if (input.title.length > MAX_TITLE_LEN) return { error: `title too long (≤${MAX_TITLE_LEN} chars)` };
      if (input.content_text.length > MAX_MARKDOWN_LEN) return { error: 'content too large' };
      if (input.project !== undefined && input.project.length > MAX_PROJECT_LEN) {
        return { error: `project too long (≤${MAX_PROJECT_LEN} chars)` };
      }

      const citations = input.citations ?? [];
      const v = validateCitations(input.content_text, citations);
      if (!v.ok) return { error: `citation validation: ${v.reason}` };

      const doc = markdownToTiptap(input.content_text);
      if (!isValidTiptapDoc(doc)) {
        logger.warn('tiptap_validation_failed', { slug: input.slug, body_len: input.content_text.length, source: 'wiki_upsert' });
        return { error: 'internal: markdown conversion produced an invalid Tiptap doc' };
      }

      try {
        const startMs = Date.now();
        const id = await upsertWikiPage(
          input.slug,
          input.title,
          doc,
          input.content_text,
          userId,
          citations,
          embedFn,
          { project: input.project, needsReview: true },
        );
        logger.info('wiki_upsert_complete', {
          slug: input.slug,
          page_id: id,
          body_len: input.content_text.length,
          citation_count: citations.length,
          duration_ms: Date.now() - startMs,
        });
        return { id, slug: input.slug, project: input.project, needs_review: true };
      } catch (err) {
        logger.error('wiki_upsert_failed', { slug: input.slug, user_id: userId }, err);
        return toolError('wiki_upsert', err);
      }
    },
  };
}
