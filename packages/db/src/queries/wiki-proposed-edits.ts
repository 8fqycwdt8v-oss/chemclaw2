import { eq, and, desc, sql } from 'drizzle-orm';
import { db } from '../client';
import { wikiProposedEdits } from '../schema/wiki-proposed-edits';

export type ProposedEditStatus = 'pending' | 'applied' | 'rejected' | 'superseded';

export type ProposedEditInput = {
  slug: string;
  title: string;
  // Stored as JSONB; shape is validated at the agent-tool seam.
  content: unknown;
  contentText: string;
  citations: Array<{ citationId: string; sourceType: string; sourceId?: string; label: string }>;
  rationale?: string;
};

export type ProposedEditRow = {
  id: string;
  slug: string;
  title: string;
  content: unknown;
  contentText: string;
  citations: unknown;
  proposedBy: string;
  rationale: string | null;
  status: ProposedEditStatus;
  previousId: string | null;
  reviewedBy: string | null;
  reviewComment: string | null;
  reviewedAt: Date | null;
  appliedPageId: string | null;
  createdAt: Date;
};

/**
 * Create a pending proposal for a slug. If a pending proposal already exists
 * for the same slug, mark it 'superseded' first and link the new row's
 * previous_id to it — keeps an audit chain of what the agent considered
 * before the reviewer approved or rejected.
 *
 * Single-row contract: callers see only the newly-inserted id. The supersede
 * + insert happen in one transaction so the queue is never momentarily empty.
 */
export async function insertProposedEdit(
  input: ProposedEditInput,
  proposedBy: string,
): Promise<{ id: string; supersededId: string | null }> {
  if (input.title.length === 0 || input.title.length > 500) {
    throw new Error('title must be 1-500 chars');
  }
  if (input.contentText.length === 0 || input.contentText.length > 500_000) {
    throw new Error('contentText must be 1-500000 chars');
  }
  return db.transaction(async (tx) => {
    // Find any pending row for this slug.
    const [existing] = await tx
      .select({ id: wikiProposedEdits.id })
      .from(wikiProposedEdits)
      .where(and(eq(wikiProposedEdits.slug, input.slug), eq(wikiProposedEdits.status, 'pending')))
      .orderBy(desc(wikiProposedEdits.createdAt))
      .limit(1);
    let supersededId: string | null = null;
    if (existing) {
      await tx
        .update(wikiProposedEdits)
        .set({ status: 'superseded', reviewedAt: sql`NOW()`, reviewComment: 'Replaced by a newer pending proposal' })
        .where(eq(wikiProposedEdits.id, existing.id));
      supersededId = existing.id;
    }
    const [inserted] = await tx
      .insert(wikiProposedEdits)
      .values({
        slug: input.slug,
        title: input.title,
        content: input.content,
        contentText: input.contentText,
        citations: input.citations,
        rationale: input.rationale ?? null,
        proposedBy,
        previousId: supersededId,
      })
      .returning({ id: wikiProposedEdits.id });
    return { id: inserted.id, supersededId };
  });
}

export async function getProposedEdit(id: string): Promise<ProposedEditRow | null> {
  const [row] = await db.select().from(wikiProposedEdits).where(eq(wikiProposedEdits.id, id));
  return (row as ProposedEditRow) ?? null;
}

/** List pending proposals, newest first. Admin review queue. */
export async function listPendingProposedEdits(limit = 50): Promise<ProposedEditRow[]> {
  const rows = await db
    .select()
    .from(wikiProposedEdits)
    .where(eq(wikiProposedEdits.status, 'pending'))
    .orderBy(desc(wikiProposedEdits.createdAt))
    .limit(Math.min(Math.max(1, limit), 200));
  return rows as ProposedEditRow[];
}

/** History for a single slug — every proposal, regardless of status. */
export async function listProposedEditsForSlug(slug: string, limit = 50): Promise<ProposedEditRow[]> {
  const rows = await db
    .select()
    .from(wikiProposedEdits)
    .where(eq(wikiProposedEdits.slug, slug))
    .orderBy(desc(wikiProposedEdits.createdAt))
    .limit(Math.min(Math.max(1, limit), 200));
  return rows as ProposedEditRow[];
}

/**
 * Mark a proposal applied and link it to the wiki_pages row that materialized
 * from it. Caller is expected to have already called upsertWikiPage and
 * passes the returned pageId. We do not call upsertWikiPage from here to
 * avoid two-phase coupling — the route stays in charge of the orchestration.
 *
 * Returns { found: false } if the id doesn't exist or the row isn't pending.
 */
export async function markProposedEditApplied(
  id: string,
  reviewedBy: string,
  appliedPageId: string,
  comment?: string,
): Promise<{ found: boolean }> {
  const rows = await db
    .update(wikiProposedEdits)
    .set({
      status: 'applied',
      reviewedBy,
      reviewedAt: sql`NOW()`,
      reviewComment: comment ?? null,
      appliedPageId,
    })
    .where(and(eq(wikiProposedEdits.id, id), eq(wikiProposedEdits.status, 'pending')))
    .returning({ id: wikiProposedEdits.id });
  return { found: rows.length > 0 };
}

/**
 * Reject a pending proposal with a required reviewer comment so the audit
 * trail captures why the change was declined.
 */
export async function markProposedEditRejected(
  id: string,
  reviewedBy: string,
  comment: string,
): Promise<{ found: boolean }> {
  if (comment.length === 0 || comment.length > 2000) {
    throw new Error('review comment must be 1-2000 chars');
  }
  const rows = await db
    .update(wikiProposedEdits)
    .set({
      status: 'rejected',
      reviewedBy,
      reviewedAt: sql`NOW()`,
      reviewComment: comment,
    })
    .where(and(eq(wikiProposedEdits.id, id), eq(wikiProposedEdits.status, 'pending')))
    .returning({ id: wikiProposedEdits.id });
  return { found: rows.length > 0 };
}
