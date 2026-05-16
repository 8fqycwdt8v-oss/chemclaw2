import { describe, it, expect, vi } from 'vitest';

vi.mock('@chemclaw2/db', () => ({
  upsertWikiPage: vi.fn().mockResolvedValue('11111111-1111-1111-1111-111111111111'),
  // sessionId is omitted in these tests so neither function actually runs, but
  // the import still has to resolve.
  replaceSessionTodos: vi.fn().mockResolvedValue(undefined),
  markAllTodosDone: vi.fn().mockResolvedValue(undefined),
}));

import { createDeepResearchTools } from '../deep-research';

const noopEmbed = async (texts: string[]) => texts.map(() => Array(1536).fill(0));

describe('createDeepResearchTools.begin', () => {
  const { begin } = createDeepResearchTools('user_test', noopEmbed);

  it('rejects an empty question', async () => {
    const r = await begin.execute({ question: '   ' });
    expect(r).toHaveProperty('error');
  });

  it('rejects a question longer than 2000 chars', async () => {
    const r = await begin.execute({ question: 'q'.repeat(2001) });
    expect(r).toHaveProperty('error');
  });

  it('returns a directive and a non-empty checklist', async () => {
    const r = (await begin.execute({ question: 'What do we know about PARP inhibitors?' })) as
      Record<string, unknown>;
    expect(r.directive).toMatch(/multi-step/);
    const checklist = r.checklist as string[];
    expect(Array.isArray(checklist)).toBe(true);
    expect(checklist.length).toBeGreaterThan(0);
    expect(checklist.some((c) => /wiki_lookup/.test(c))).toBe(true);
    expect(checklist.some((c) => /finalize_deep_research/.test(c))).toBe(true);
  });
});

describe('createDeepResearchTools.finalize', () => {
  const { finalize } = createDeepResearchTools('user_test', noopEmbed);

  it('rejects an invalid slug', async () => {
    const r = await finalize.execute({
      slug: 'NOT VALID',
      title: 'Title',
      body: 'Body text.',
    });
    expect(r).toHaveProperty('error');
  });

  it('rejects a too-long body', async () => {
    const r = await finalize.execute({
      slug: 'parp-inhibitors-2026',
      title: 'PARP Inhibitors 2026',
      body: 'x'.repeat(500_001),
    });
    expect(r).toHaveProperty('error');
  });

  it('rejects a body that references [1] without supplying that citation', async () => {
    const r = await finalize.execute({
      slug: 'parp-inhibitors-2026',
      title: 'PARP Inhibitors 2026',
      body: 'Olaparib was approved in 2014 [1].',
      citations: [],
    });
    expect(r).toHaveProperty('error');
    expect((r as { error: string }).error).toMatch(/citation/);
  });

  it('rejects a URL citation pointing to a non-allowlisted domain', async () => {
    const r = await finalize.execute({
      slug: 'parp-inhibitors-2026',
      title: 'PARP Inhibitors 2026',
      body: 'A claim [1].',
      citations: [
        { citationId: '1', sourceType: 'url', sourceId: 'https://attacker.example.com/x', label: 'evil' },
      ],
    });
    expect(r).toHaveProperty('error');
    expect((r as { error: string }).error).toMatch(/allowed science-domain/);
  });

  it('persists and returns the wiki page id when validation passes', async () => {
    const r = (await finalize.execute({
      slug: 'parp-inhibitors-2026',
      title: 'PARP Inhibitors 2026',
      body: 'Olaparib (CAS 763113-22-0) was approved by the FDA in 2014.',
      citations: [],
    })) as Record<string, unknown>;
    expect(r.wiki_page_id).toBe('11111111-1111-1111-1111-111111111111');
    expect(r.slug).toBe('parp-inhibitors-2026');
    expect(r.needs_review).toBe(true);
  });
});
