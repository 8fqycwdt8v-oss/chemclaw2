import Link from 'next/link';
import { notFound } from 'next/navigation';
import { auth } from '@clerk/nextjs/server';
import { getWikiPage, getWikiPageCitations, listWikiRevisions, isSubscribed } from '@chemclaw2/db';
import { WikiPageView } from '@/components/wiki/WikiPageView';

type Props = { params: Promise<{ slug: string }> };

export default async function WikiSlugPage({ params }: Props) {
  const { slug } = await params;
  if (slug === 'new') {
    return <NewPageStub />;
  }
  const page = await getWikiPage(slug);
  if (!page) notFound();

  const { userId } = await auth();
  const [citations, revisions, subscribed] = await Promise.all([
    getWikiPageCitations(page.id),
    listWikiRevisions(page.id, 10).catch(() => []),
    userId ? isSubscribed(userId, page.id).catch(() => false) : Promise.resolve(false),
  ]);

  return (
    <WikiPageView
      slug={page.slug}
      title={page.title}
      content={page.content}
      contentText={page.contentText}
      version={page.version}
      updatedAt={page.updatedAt.toISOString()}
      updatedBy={page.updatedBy}
      needsReview={page.needsReview}
      archived={page.archived}
      maturity={page.maturity}
      project={page.project}
      subscribed={subscribed}
      citations={citations.map((c) => ({
        citationId: c.citationId,
        sourceType: c.sourceType,
        sourceId: c.sourceId ?? undefined,
        label: c.label,
        disputed: (c as { disputed?: boolean }).disputed ?? false,
      }))}
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
        New pages are created via <code>POST /api/wiki</code> or by asking the agent to use the
        <code> wiki_upsert</code> tool. Pick a slug, title, and content.
      </p>
      <Link href="/wiki" className="text-sm text-blue-600">← Back to wiki</Link>
    </div>
  );
}
