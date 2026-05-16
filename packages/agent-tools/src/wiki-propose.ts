import { z } from 'zod';
import { insertProposedEdit } from '@chemclaw2/db';
import { isValidSlug } from './slug';
import { markdownToTiptap } from './markdown-to-tiptap';
import { validateCitations } from './citation-validation';
import type { ToolDef } from './tool-def';

const wikiProposeSchema = {
  slug: z.string().describe('Lowercase kebab-case page slug'),
  title: z.string().describe('Human-readable title'),
  content_text: z.string().describe('Markdown body of the proposed page'),
  rationale: z.string().optional().describe(
    'Short explanation of WHY this edit is proposed (≤2000 chars)',
  ),
  citations: z.array(z.object({
    citationId: z.string(),
    sourceType: z.string(),
    sourceId: z.string().optional(),
    label: z.string(),
  })).optional(),
};

/**
 * Wave-3c opportunity #1: stage a wiki edit for human review instead of
 * writing directly. Mirrors `wiki_upsert`'s validation surface so the agent
 * doesn't have to learn two contracts — the only delta is that the result
 * lands in `wiki_proposed_edits.pending` rather than `wiki_pages`.
 *
 * An admin reviews via /api/admin/wiki/proposed-edits and approves
 * (→ apply, which calls upsertWikiPage) or rejects with a comment.
 *
 * When to call this vs. wiki_upsert: prefer propose_wiki_edit for any
 * change to a high-maturity / canonical page or any change a reviewer
 * is likely to want to see before it lands. wiki_upsert remains the
 * right tool for freshly-authored agent pages flagged needs_review=true.
 */
export function createWikiProposeTool(userId: string): ToolDef<typeof wikiProposeSchema> {
  return {
    name: 'propose_wiki_edit',
    description:
      'Stage a wiki edit for human review. Same shape as wiki_upsert but ' +
      'the result lands in a review queue instead of overwriting the live ' +
      'page. Use for any change to a validated/authoritative page, or when ' +
      'the user has asked you to "draft" or "propose" rather than "save". ' +
      'A rationale field is recommended so the reviewer can see why the ' +
      'change was suggested.',
    schema: wikiProposeSchema,
    async execute(input) {
      if (!isValidSlug(input.slug)) {
        return { error: 'slug must be lowercase kebab-case, ≤200 chars' };
      }
      if (input.title.length === 0 || input.title.length > 500) {
        return { error: 'title must be 1-500 chars' };
      }
      if (input.content_text.length === 0 || input.content_text.length > 500_000) {
        return { error: 'content_text must be 1-500000 chars' };
      }
      if (input.rationale != null && input.rationale.length > 2000) {
        return { error: 'rationale must be ≤2000 chars' };
      }

      const citations = input.citations ?? [];
      const v = validateCitations(input.content_text, citations);
      if (!v.ok) return { error: `citation validation: ${v.reason}` };

      try {
        const result = await insertProposedEdit({
          slug: input.slug,
          title: input.title,
          content: markdownToTiptap(input.content_text) as unknown as Record<string, unknown>,
          contentText: input.content_text,
          citations,
          rationale: input.rationale,
        }, userId);
        return {
          proposal_id: result.id,
          slug: input.slug,
          superseded_id: result.supersededId,
          status: 'pending',
          note: 'Proposal queued for human review. A reviewer will approve or reject before the change lands.',
        };
      } catch (err) {
        return { error: err instanceof Error ? err.message : 'propose_wiki_edit failed' };
      }
    },
  };
}
