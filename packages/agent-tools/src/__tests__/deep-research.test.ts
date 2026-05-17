import { describe, it, expect, vi } from 'vitest';

vi.mock('@chemclaw2/db', () => ({
  upsertWikiPage: vi.fn().mockResolvedValue('11111111-1111-1111-1111-111111111111'),
  markAllTodosDone: vi.fn().mockResolvedValue(undefined),
}));

import { createDeepResearchTools } from '../deep-research';

const noopEmbed = async (texts: string[]) => texts.map(() => Array(1536).fill(0));

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
