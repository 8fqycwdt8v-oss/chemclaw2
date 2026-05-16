import { upsertWikiPage } from '@chemclaw2/db';
import { isValidSlug } from './slug';

/**
 * Factory: lets the agent write or update a wiki page on the user's behalf.
 * userId is captured at factory time so the LLM cannot impersonate someone else.
 *
 * This is the write-side counterpart of wikiFetchTool — closes §3.4
 * "publish calculation result into a wiki page section" without a new route.
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
      'kebab-case (e.g. "aspirin-synthesis").',
    inputSchema: {
      type: 'object' as const,
      properties: {
        slug: { type: 'string', description: 'Lowercase kebab-case page slug' },
        title: { type: 'string', description: 'Human-readable title' },
        content_text: {
          type: 'string',
          description: 'Plain-text content (gets chunked + embedded for semantic search)',
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
      if (!isValidSlug(input.slug)) {
        return { error: 'slug must be lowercase kebab-case, ≤200 chars' };
      }
      if (input.title.length > 500) return { error: 'title too long (≤500 chars)' };
      if (input.content_text.length > 500_000) return { error: 'content too large' };
      try {
        const id = await upsertWikiPage(
          input.slug,
          input.title,
          // Minimal Tiptap doc — a single paragraph the user can later restructure
          { type: 'doc', content: [{ type: 'paragraph', content: [{ type: 'text', text: input.content_text }] }] },
          input.content_text,
          userId,
          input.citations ?? [],
          embedFn,
        );
        // project is set via PATCH after creation if requested
        return { id, slug: input.slug, project: input.project };
      } catch (err) {
        return { error: err instanceof Error ? err.message : 'wiki_upsert failed' };
      }
    },
  };
}
