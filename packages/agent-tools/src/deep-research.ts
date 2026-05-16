import { upsertWikiPage, replaceSessionTodos, markAllTodosDone } from '@chemclaw2/db';
import { isValidSlug } from './slug';
import { markdownToTiptap } from './markdown-to-tiptap';
import { validateCitations } from './citation-validation';

/**
 * Deep research: a two-tool workflow the agent runs to produce a structured
 * research deliverable.
 *   1. begin_deep_research(question) → returns a research-mode directive +
 *      checklist the agent uses to plan its own multi-step investigation.
 *      Uses the agent's existing tool surface (wiki_lookup, similarity search,
 *      web_search, fetch_document) — no separate sub-agent dispatch.
 *   2. finalize_deep_research(slug, title, body, citations) → persists the
 *      composed report as a wiki page via the same upsertWikiPage path the
 *      campaign worker uses.
 *
 * v2.1-B2: the checklist is also persisted as session todos so the chat UI can
 * surface a running progress list. sessionId is optional — when absent (e.g.
 * isolated tests) the persistence path is skipped.
 *
 * Designed to be small: the SDK's normal multi-turn loop drives the research;
 * these two tools just shape the start and finish.
 */
export function createDeepResearchTools(
  userId: string,
  embedFn: (texts: string[]) => Promise<number[][]>,
  sessionId?: string,
) {
  const begin = {
    name: 'begin_deep_research',
    description:
      'Start a deep-research workflow. Returns a structured plan checklist + ' +
      'research-mode directive the agent should follow. Call this when the user ' +
      'asks for a multi-section report, a comprehensive review, or a /dr-style ' +
      'investigation. Use the returned plan to drive the next several turns.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        question: { type: 'string', description: 'The research question.' },
      },
      required: ['question'],
    },
    async execute(input: { question: string }) {
      const q = input.question.trim();
      if (q.length === 0 || q.length > 2000) return { error: 'question must be 1-2000 chars' };
      const checklist = [
        'Search the wiki for the topic via wiki_lookup (try slug + FTS query + semantic).',
        'If a SMILES is involved, run compound_similarity_search to ground in registered compounds.',
        'If a reaction/transformation is involved, run find_similar_reactions for prior precedent.',
        'Pull at least 2 external sources via web_search → fetch_document for context.',
        'Compose the report as 3-6 sections with inline [N] citations.',
        'Finally, call finalize_deep_research with the slug, title, body, and citation entries.',
      ];
      if (sessionId) {
        await replaceSessionTodos(sessionId, userId, checklist).catch((err) => {
          // Persistence failure must not block the research workflow itself.
          console.error('[deep-research] replaceSessionTodos failed:', err);
        });
      }
      return {
        question: q,
        directive: [
          'Conduct a multi-step investigation. Plan first, then execute the plan.',
          'Hard rules:',
          '- Every non-trivial claim must trace to a tool call you actually made.',
          '- Cite each claim with an inline marker [N] and include a citation entry.',
          '- Never invent CAS numbers, yields, or experimental conditions.',
          '- When evidence is thin, say "weak support" and propose follow-up tools to run.',
        ].join('\n'),
        checklist,
      };
    },
  };

  const finalize = {
    name: 'finalize_deep_research',
    description:
      'Persist the composed research report as a wiki page. Call this after ' +
      'begin_deep_research and after the report body is fully drafted with ' +
      'inline citations.',
    inputSchema: {
      type: 'object' as const,
      properties: {
        slug: { type: 'string', description: 'Lowercase kebab-case slug (e.g. parp-inhibitor-sar-2026)' },
        title: { type: 'string', description: 'Human-readable title' },
        body: { type: 'string', description: 'Full report body (markdown).' },
        citations: {
          type: 'array',
          description: 'Inline [N] citation entries used in the body.',
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
      required: ['slug', 'title', 'body'],
    },
    async execute(input: {
      slug: string;
      title: string;
      body: string;
      citations?: Array<{ citationId: string; sourceType: string; sourceId?: string; label: string }>;
    }) {
      if (!isValidSlug(input.slug)) {
        return { error: 'slug must be lowercase kebab-case, ≤200 chars, and not reserved' };
      }
      if (input.title.length === 0 || input.title.length > 500) return { error: 'title 1-500 chars' };
      if (input.body.length === 0 || input.body.length > 500_000) return { error: 'body too large' };

      const citations = input.citations ?? [];
      const v = validateCitations(input.body, citations);
      if (!v.ok) return { error: `citation validation: ${v.reason}` };

      const id = await upsertWikiPage(
        input.slug,
        input.title,
        markdownToTiptap(input.body) as unknown as Record<string, unknown>,
        input.body,
        userId,
        citations,
        embedFn,
        { needsReview: true },
      );
      if (sessionId) {
        await markAllTodosDone(sessionId).catch((err) => {
          console.error('[deep-research] markAllTodosDone failed:', err);
        });
      }
      return { wiki_page_id: id, slug: input.slug, url: `/wiki/${input.slug}`, needs_review: true };
    },
  };

  return { begin, finalize };
}
