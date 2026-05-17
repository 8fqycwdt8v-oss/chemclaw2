import { eq, and, desc, sql } from 'drizzle-orm';
import { db } from '../client';
import { wikiProposedEdits } from '../schema/wiki-proposed-edits';

export type ProposedEditStatus = 'pending' | 'applied' | 'rejected' | 'superseded';

export type ProposedEditInput = {
  slug: string;
  title: string;
  content: Record<string, unknown>;
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

/**
 * Wave-3h perf: cheap COUNT(*) for the admin nav badge. The previous
 * pattern (`listPendingProposedEdits(50).then(p => p.length)`) pulled the
 * full row including content + contentText + citations JSONB on every
 * layout render — potentially hundreds of KB. The partial index
 * `wiki_proposed_edits_pending_idx` makes this an index-only scan.
 */
export async function countPendingProposedEdits(): Promise<number> {
  const rows = await db.execute<{ count: string | number }>(sql`
    SELECT COUNT(*)::int AS count FROM wiki_proposed_edits WHERE status = 'pending'
  `);
  return Number(rows[0]?.count ?? 0);
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
 * Wave-3h fix for the security-audit-flagged TOCTOU between the apply
 * route's status read and its wiki page write: claim the proposal
 * atomically BEFORE issuing the wiki write. Acquires `SELECT … FOR UPDATE`
 * on the proposal row and moves status to 'applied' inside the same
 * transaction. A concurrent rejecter / second-apply attempt will block on
 * the row lock and then see status='applied' on retry — guaranteed that
 * exactly one reviewer wins.
 *
 * Returns the locked proposal so the caller can replay it through
 * upsertWikiPage. The proposal is ALREADY MARKED APPLIED in the DB at
 * this point; on a wiki-write failure the caller MUST call
 * `rollbackApplyClaim` to restore status='pending' (so a retry is possible).
 *
 * `appliedPageId` is left null in this step — caller fills it in with
 * `setAppliedPageId` once the wiki upsert succeeds.
 */
export async function tryClaimProposedEditForApply(
  id: string,
  reviewedBy: string,
  comment?: string,
): Promise<ProposedEditRow | null> {
  return db.transaction(async (tx) => {
    const rows = await tx.execute<ProposedEditRow>(sql`
      SELECT id, slug, title, content,
             content_text AS "contentText",
             citations,
             proposed_by AS "proposedBy",
             rationale,
             status,
             previous_id AS "previousId",
             reviewed_by AS "reviewedBy",
             review_comment AS "reviewComment",
             reviewed_at AS "reviewedAt",
             applied_page_id AS "appliedPageId",
             created_at AS "createdAt"
      FROM wiki_proposed_edits
      WHERE id = ${id} AND status = 'pending'
      FOR UPDATE
    `);
    if (rows.length === 0) return null;
    const proposal = rows[0];
    await tx
      .update(wikiProposedEdits)
      .set({
        status: 'applied',
        reviewedBy,
        reviewedAt: sql`NOW()`,
        reviewComment: comment ?? null,
      })
      .where(eq(wikiProposedEdits.id, id));
    return proposal;
  });
}

/** Wave-3h: link the applied proposal to the wiki page after the upsert. */
export async function setAppliedPageId(id: string, pageId: string): Promise<void> {
  await db
    .update(wikiProposedEdits)
    .set({ appliedPageId: pageId })
    .where(eq(wikiProposedEdits.id, id));
}

/**
 * Wave-3h: roll back an apply claim when the wiki write fails after we've
 * already moved the proposal to status='applied'. Restores status='pending'
 * so a retry can succeed. The status check guards against rolling back a
 * proposal that has since been touched by another path.
 */
export async function rollbackApplyClaim(id: string): Promise<void> {
  await db
    .update(wikiProposedEdits)
    .set({
      status: 'pending',
      reviewedBy: null,
      reviewedAt: null,
      reviewComment: null,
    })
    .where(and(eq(wikiProposedEdits.id, id), eq(wikiProposedEdits.status, 'applied')));
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
