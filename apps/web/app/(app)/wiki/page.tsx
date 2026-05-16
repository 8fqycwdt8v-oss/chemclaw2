import Link from 'next/link';
import { listWikiPages, listWikiProjects, searchWikiByFTS } from '@chemclaw2/db';

type Props = { searchParams: Promise<{ q?: string; project?: string; archived?: string }> };

export default async function WikiListPage({ searchParams }: Props) {
  const { q, project, archived } = await searchParams;
  const includeArchived = archived === '1';
  const projects = await listWikiProjects().catch(() => []);

  const pages = q && q.trim()
    ? (await searchWikiByFTS(q, 50)).map((p) => ({
        id: p.id, slug: p.slug, title: p.title, updatedAt: null as Date | null,
        maturity: 'exploratory', needsReview: false, archived: false, project: null as string | null,
      }))
    : (await listWikiPages(50, undefined, { project, includeArchived })).map((p) => ({
        id: p.id, slug: p.slug, title: p.title, updatedAt: p.updatedAt as Date,
        maturity: p.maturity, needsReview: p.needsReview, archived: p.archived, project: p.project,
      }));

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
        {project && <input type="hidden" name="project" value={project} />}
        <button className="border rounded px-3 py-2 text-sm" type="submit">Search</button>
      </form>
      {projects.length > 0 && (
        <div className="flex flex-wrap gap-1 text-xs">
          <Link href="/wiki" className={`px-2 py-1 rounded ${!project ? 'bg-slate-800 text-white' : 'bg-slate-100 text-slate-700'}`}>
            all
          </Link>
          {projects.map((proj) => (
            <Link key={proj} href={`/wiki?project=${encodeURIComponent(proj)}`}
              className={`px-2 py-1 rounded ${project === proj ? 'bg-purple-600 text-white' : 'bg-purple-50 text-purple-700'}`}>
              {proj}
            </Link>
          ))}
          <Link href={`/wiki?${includeArchived ? '' : 'archived=1'}`}
            className={`px-2 py-1 rounded ml-2 ${includeArchived ? 'bg-slate-500 text-white' : 'bg-slate-50 text-slate-600'}`}>
            {includeArchived ? '✓ archived' : 'show archived'}
          </Link>
        </div>
      )}
      {pages.length === 0 ? (
        <div className="text-slate-500 text-sm">No pages{q ? ` matching "${q}"` : ''} yet.</div>
      ) : (
        <ul className="divide-y border rounded">
          {pages.map((p) => (
            <li key={p.id} className="p-3 hover:bg-slate-50">
              <div className="flex items-baseline gap-2">
                <Link href={`/wiki/${p.slug}`} className="font-medium">{p.title}</Link>
                {p.needsReview && <span className="px-1.5 py-0.5 text-xs rounded bg-amber-100 text-amber-800">needs review</span>}
                {p.archived && <span className="px-1.5 py-0.5 text-xs rounded bg-slate-200 text-slate-700">archived</span>}
                {p.maturity !== 'exploratory' && (
                  <span className={`px-1.5 py-0.5 text-xs rounded ${p.maturity === 'authoritative' ? 'bg-indigo-100 text-indigo-800' : 'bg-emerald-100 text-emerald-800'}`}>
                    {p.maturity}
                  </span>
                )}
                {p.project && (
                  <span className="px-1.5 py-0.5 text-xs rounded bg-purple-50 text-purple-700">{p.project}</span>
                )}
              </div>
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
