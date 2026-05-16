import Link from 'next/link';
import { notFound } from 'next/navigation';
import { getWikiPage, getWikiPageCitations, listWikiRevisions } from '@chemclaw2/db';
import { WikiPageView } from '@/components/wiki/WikiPageView';

type Props = { params: Promise<{ slug: string }> };

export default async function WikiSlugPage({ params }: Props) {
  const { slug } = await params;
  if (slug === 'new') {
    return <NewPageStub />;
  }
  const page = await getWikiPage(slug);
  if (!page) notFound();

  const citations = await getWikiPageCitations(page.id);
  const revisions = await listWikiRevisions(page.id, 10).catch(() => []);

  return (
    <WikiPageView
      slug={page.slug}
      title={page.title}
      content={page.content as Record<string, unknown>}
      contentText={page.contentText ?? ''}
      version={page.version}
      updatedAt={page.updatedAt.toISOString()}
      updatedBy={page.updatedBy ?? page.createdBy}
      citations={citations.map((c) => ({ ...c, sourceId: c.sourceId ?? undefined }))}
      revisions={revisions.map((r) => ({
        version: r.version,
        updatedAt: new Date(r.updatedAt as string | number | Date).toISOString(),
        updatedBy: r.updatedBy ?? '',
      }))}
    />
  );
}

function NewPageStub() {
  return (
    <div className="space-y-4">
      <h1 className="text-xl font-semibold">New wiki page</h1>
      <p className="text-sm text-slate-600">
        New pages are created via <code>POST /api/wiki</code>. Pick a slug, title, and content (Tiptap JSON).
        For now, use the agent (it can create pages via the wiki_lookup tool flow) or POST directly.
      </p>
      <Link href="/wiki" className="text-sm text-blue-600">← Back to wiki</Link>
    </div>
  );
}
