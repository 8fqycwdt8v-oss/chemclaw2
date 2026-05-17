import { z } from 'zod';
import {
  isValidSlug,
  MAX_TITLE_LEN, MAX_MARKDOWN_LEN, MAX_CITATIONS, MAX_PROJECT_LEN,
  TiptapDocSchema,
} from '@chemclaw2/agent-tools';

const MAX_CITATION_FIELD_LEN = 1_000;

export const SlugSchema = z.string().refine(isValidSlug, { message: 'Invalid slug' });

export const CitationSchema = z.object({
  citationId: z.string().min(1).max(MAX_CITATION_FIELD_LEN),
  sourceType: z.string().min(1).max(MAX_CITATION_FIELD_LEN),
  sourceId: z.string().max(MAX_CITATION_FIELD_LEN).optional(),
  label: z.string().min(1).max(MAX_CITATION_FIELD_LEN),
});

export const CitationsSchema = z.array(CitationSchema).max(MAX_CITATIONS);

export const WikiPostBodySchema = z.object({
  slug: z.string().min(1),
  title: z.string().min(1).max(MAX_TITLE_LEN),
  // content is loose unless present — pass-through to Tiptap if undefined
  content: TiptapDocSchema.optional(),
  contentText: z.string().max(MAX_MARKDOWN_LEN).optional(),
  citations: CitationsSchema.optional(),
});

export const WikiPutBodySchema = z.object({
  title: z.string().min(1).max(MAX_TITLE_LEN).optional(),
  content: TiptapDocSchema.optional(),
  contentText: z.string().max(MAX_MARKDOWN_LEN).optional(),
  citations: CitationsSchema.optional(),
});

export const WikiPatchBodySchema = z
  .object({
    needsReview: z.boolean().optional(),
    archived: z.boolean().optional(),
    maturity: z.enum(['exploratory', 'validated', 'authoritative']).optional(),
    project: z.string().max(MAX_PROJECT_LEN).nullable().optional(),
  })
  .refine(
    (v) => Object.values(v).some((x) => x !== undefined),
    { message: 'no metadata fields provided' },
  );

// zodErrorResponse moved to ./api-gate to centralize the response envelope.
export { zodErrorResponse } from './api-gate';
