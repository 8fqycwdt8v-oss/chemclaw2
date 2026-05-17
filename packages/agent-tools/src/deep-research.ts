import { z } from 'zod';
import { upsertWikiPage, markAllTodosDone } from '@chemclaw2/db';
import { logger } from '@chemclaw2/observability';
import { isValidSlug } from './slug';
import { markdownToTiptap } from './markdown-to-tiptap';
import { validateCitations } from './citation-validation';
import { isValidTiptapDoc } from './tiptap';
import { toolError } from './tool-error';
import { MAX_TITLE_LEN, MAX_MARKDOWN_LEN } from './limits';
import type { ToolDef } from './tool-def';

const finalizeSchema = {
  slug: z.string().describe('Lowercase kebab-case slug (e.g. parp-inhibitor-sar-2026)'),
  title: z.string().describe('Human-readable title'),
  body: z.string().describe('Full report body (markdown).'),
  citations: z.array(z.object({
    citationId: z.string(),
    sourceType: z.string().describe('e.g. "compound", "reaction", "url", "doc"'),
    sourceId: z.string().optional(),
    label: z.string(),
  })).optional().describe('Inline [N] citation entries used in the body.'),
};

/**
 * Deep research: agent runs the investigation in-line (the SDK Task-tool
 * sub-agent path handles planning), then calls finalize_deep_research to
 * persist the composed report.
 *
 * The pre-v2.x `begin_deep_research` kickoff tool was removed in v2.x: the
 * planning checklist now lives in the sub-agent system prompt
 * (apps/web/lib/subagent-prompts.ts) and session todos are no longer seeded
 * automatically.
 */
export function createDeepResearchTools(
  userId: string,
  embedFn: (texts: string[]) => Promise<number[][]>,
  sessionId?: string,
): { finalize: ToolDef<typeof finalizeSchema> } {
  const finalize: ToolDef<typeof finalizeSchema> = {
    name: 'finalize_deep_research',
    description:
      'Persist the composed research report as a wiki page. Call this once ' +
      'the report body is fully drafted with inline [N] citations.',
    schema: finalizeSchema,
    async execute(input) {
      if (!isValidSlug(input.slug)) {
        return { error: 'slug must be lowercase kebab-case, ≤200 chars, and not reserved' };
      }
      if (input.title.length === 0 || input.title.length > MAX_TITLE_LEN) {
        return { error: `title 1-${MAX_TITLE_LEN} chars` };
      }
      if (input.body.length === 0 || input.body.length > MAX_MARKDOWN_LEN) {
        return { error: 'body too large' };
      }

      const citations = input.citations ?? [];
      const v = validateCitations(input.body, citations);
      if (!v.ok) return { error: `citation validation: ${v.reason}` };

      const doc = markdownToTiptap(input.body);
      if (!isValidTiptapDoc(doc)) {
        logger.warn('tiptap_validation_failed', { slug: input.slug, body_len: input.body.length, source: 'deep_research' });
        return { error: 'internal: markdown conversion produced an invalid Tiptap doc' };
      }

      try {
        const startMs = Date.now();
        const id = await upsertWikiPage(
          input.slug,
          input.title,
          doc,
          input.body,
          userId,
          citations,
          embedFn,
          { needsReview: true },
        );
        logger.info('deep_research_finalized', {
          slug: input.slug,
          page_id: id,
          body_len: input.body.length,
          citation_count: citations.length,
          duration_ms: Date.now() - startMs,
        });
        if (sessionId) {
          await markAllTodosDone(sessionId).catch((err) => {
            logger.error('mark_all_todos_done_failed', { session_id: sessionId }, err);
          });
        }
        return { wiki_page_id: id, slug: input.slug, url: `/wiki/${input.slug}`, needs_review: true };
      } catch (err) {
        logger.error('deep_research_upsert_failed', { slug: input.slug }, err);
        return toolError('finalize_deep_research', err);
      }
    },
  };

  return { finalize };
}
