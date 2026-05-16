import { upsertWikiPage } from '@chemclaw2/db';
import { markdownToTiptap } from './markdown-to-tiptap';
import { validateCitations } from './citation-validation';

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
) {
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
    inputSchema: {
      type: 'object' as const,
      properties: {
        slug: { type: 'string', description: 'Lowercase kebab-case page slug' },
        title: { type: 'string', description: 'Human-readable title' },
        content_text: {
          type: 'string',
          description: 'Markdown body (gets parsed into Tiptap, then chunked + embedded)',
        },
        project: {
          type: 'string',
          description: 'Optional project tag for scoping (e.g. "project-x")',
        },
        citations: {
          type: 'array',
          description: 'Sources used (compounds, reactions, URLs, doc IDs)',
          items: {
            type: 'object',
            properties: {
              citationId: { type: 'string' },
              sourceType: { type: 'string', description: 'e.g. "compound", "reaction", "url", "doc"' },
              sourceId: { type: 'string' },
              label: { type: 'string' },
            },
            required: ['citationId', 'sourceType', 'label'],
          },
        },
      },
      required: ['slug', 'title', 'content_text'],
    },
    async execute(input: {
      slug: string;
      title: string;
      content_text: string;
      project?: string;
      citations?: Array<{ citationId: string; sourceType: string; sourceId?: string; label: string }>;
    }) {
      if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(input.slug) || input.slug.length > 200) {
        return { error: 'slug must be lowercase kebab-case, ≤200 chars' };
      }
      if (input.title.length > 500) return { error: 'title too long (≤500 chars)' };
      if (input.content_text.length > 500_000) return { error: 'content too large' };
      if (input.project !== undefined && input.project.length > 100) {
        return { error: 'project too long (≤100 chars)' };
      }

      const citations = input.citations ?? [];
      const v = validateCitations(input.content_text, citations);
      if (!v.ok) return { error: `citation validation: ${v.reason}` };

      try {
        const id = await upsertWikiPage(
          input.slug,
          input.title,
          markdownToTiptap(input.content_text) as unknown as Record<string, unknown>,
          input.content_text,
          userId,
          citations,
          embedFn,
          { project: input.project, needsReview: true },
        );
        return { id, slug: input.slug, project: input.project, needs_review: true };
      } catch (err) {
        return { error: err instanceof Error ? err.message : 'wiki_upsert failed' };
      }
    },
  };
}
