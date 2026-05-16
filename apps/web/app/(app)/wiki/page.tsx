import Link from 'next/link';
import { listWikiPages, searchWikiByFTS } from '@chemclaw2/db';

type Props = { searchParams: Promise<{ q?: string }> };

export default async function WikiListPage({ searchParams }: Props) {
  const { q } = await searchParams;
  const pages = q && q.trim()
    ? (await searchWikiByFTS(q, 50)).map((p) => ({ id: p.id, slug: p.slug, title: p.title, updatedAt: null as Date | null }))
    : (await listWikiPages(50)).map((p) => ({ id: p.id, slug: p.slug, title: p.title, updatedAt: p.updatedAt as Date }));

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">Wiki</h1>
        <Link href="/wiki/new" className="text-sm text-blue-600">New page</Link>
      </div>
      <form className="flex gap-2">
        <input
          name="q"
          defaultValue={q ?? ''}
          placeholder="Search wiki…"
          className="flex-1 border rounded px-3 py-2 text-sm"
        />
        <button className="border rounded px-3 py-2 text-sm" type="submit">Search</button>
      </form>
      {pages.length === 0 ? (
        <div className="text-slate-500 text-sm">No pages{q ? ` matching "${q}"` : ''} yet.</div>
      ) : (
        <ul className="divide-y border rounded">
          {pages.map((p) => (
            <li key={p.id} className="p-3 hover:bg-slate-50">
              <Link href={`/wiki/${p.slug}`} className="font-medium">{p.title}</Link>
              <div className="text-xs text-slate-500 mt-0.5">
                {p.slug}{p.updatedAt ? ` · updated ${p.updatedAt.toISOString().slice(0, 10)}` : ''}
              </div>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
