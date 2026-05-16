import { z } from 'zod';
import { upsertPaper } from '@chemclaw2/db';
import type { ToolDef } from './tool-def';

const schema = {
  title: z.string().describe('Paper title (required, 1-1000 chars)'),
  doi: z.string().optional().describe('DOI in 10.NNNN/... form'),
  pubmed_id: z.string().optional().describe('PubMed numeric id'),
  url: z.string().optional(),
  abstract: z.string().optional(),
  content_text: z.string().optional().describe('Optional full-text extract for FTS'),
};

// Conservative DOI shape — prefix `10.<registrant>/<suffix>`. Won't catch
// every variant (e.g. shortDOIs) but blocks junk strings.
const DOI_RE = /^10\.\d{4,9}\/[-._;()/:A-Z0-9]+$/i;
const PUBMED_RE = /^\d{1,8}$/;

/**
 * Wave-3e B6 write tool: promote a frequently-cited paper to a structured
 * row in the `papers` table. The entity-extractor sub-agent calls this after
 * parsing a wiki citation that points to a DOI or PubMed URL.
 *
 * Upsert semantics: same DOI or pubmed_id → existing row's title / url /
 * abstract / content_text refreshed (per upsertPaper). Caller gets the
 * returned id and can store it in wiki_citations.source_id to tighten the
 * citation→paper link.
 */
export function createRegisterPaperTool(userId: string): ToolDef<typeof schema> {
  return {
    name: 'register_paper',
    description:
      'Persist a paper as a structured entity (DOI / PubMed id / abstract). ' +
      'Use after recognizing a literature citation in a wiki body. Returns ' +
      'the paper id; on upsert against an existing DOI/PubMed match, returns ' +
      'the existing id and refreshes the title/abstract/content_text fields.',
    subagents: ['entity-extractor'],
    schema,
    async execute(input) {
      if (input.title.length === 0 || input.title.length > 1000) {
        return { error: 'title must be 1-1000 chars' };
      }
      if (input.doi != null && !DOI_RE.test(input.doi)) {
        return { error: 'doi must match 10.NNNN/SUFFIX shape' };
      }
      if (input.pubmed_id != null && !PUBMED_RE.test(input.pubmed_id)) {
        return { error: 'pubmed_id must be a numeric string ≤8 digits' };
      }
      if (input.url != null) {
        try { new URL(input.url); } catch { return { error: 'url is not a valid URL' }; }
      }
      try {
        const { id } = await upsertPaper({
          title: input.title,
          doi: input.doi,
          pubmedId: input.pubmed_id,
          url: input.url,
          abstract: input.abstract,
          contentText: input.content_text,
        }, userId);
        return { id, doi: input.doi ?? null, pubmed_id: input.pubmed_id ?? null };
      } catch (err) {
        return { error: err instanceof Error ? err.message : 'upsertPaper failed' };
      }
    },
  };
}
