import { describe, it, expect, vi, beforeEach } from 'vitest';

const mocks = vi.hoisted(() => ({
  selectRows: [] as unknown[],
  insertCaptured: [] as unknown[],
  updateCaptured: [] as Array<{ set: Record<string, unknown> }>,
  returningResults: [] as unknown[],
  // Wave-3h: tryClaimProposedEditForApply uses tx.execute(sql`...`). Tests
  // push rows here to seed each execute() result in FIFO.
  executeRows: [] as unknown[][],
  txTouched: false,
}));

vi.mock('../client', () => {
  // Drizzle's select() chain — we model the minimal surface used by the
  // queries: .from().where().orderBy().limit() and direct .from().where().
  const buildSelect = () => {
    const result = mocks.selectRows;
    return {
      from: () => ({
        where: () => Object.assign(Promise.resolve(result), {
          orderBy: () => Object.assign(Promise.resolve(result), {
            limit: () => Promise.resolve(result),
          }),
          limit: () => Promise.resolve(result),
        }),
      }),
    };
  };
  // The returningResults queue is consumed in FIFO; tests must push a result
  // for each insert/update they expect to succeed. An empty queue means the
  // write touched no rows (returning [] → caller observes found:false).
  const buildInsert = () => ({
    values: (vals: unknown) => {
      mocks.insertCaptured.push(vals);
      return {
        returning: () => Promise.resolve(mocks.returningResults.length
          ? [mocks.returningResults.shift()] : []),
      };
    },
  });
  const buildUpdate = () => ({
    set: (s: Record<string, unknown>) => {
      mocks.updateCaptured.push({ set: s });
      return {
        where: () => ({
          returning: () => Promise.resolve(mocks.returningResults.length
            ? [mocks.returningResults.shift()] : []),
        }),
      };
    },
  });
  const execute = () => Promise.resolve(mocks.executeRows.shift() ?? []);
  return {
    db: {
      select: buildSelect,
      insert: buildInsert,
      update: buildUpdate,
      execute,
      transaction: async (fn: (tx: unknown) => Promise<unknown>) => {
        mocks.txTouched = true;
        return fn({
          execute,
          select: buildSelect,
          insert: buildInsert,
          update: buildUpdate,
        });
      },
    },
  };
});

import {
  insertProposedEdit,
  getProposedEdit,
  listPendingProposedEdits,
  markProposedEditApplied,
  markProposedEditRejected,
  tryClaimProposedEditForApply,
  setAppliedPageId,
  rollbackApplyClaim,
  countPendingProposedEdits,
} from '../queries/wiki-proposed-edits';

const baseInput = {
  slug: 'aspirin-synthesis-2026',
  title: 'Aspirin synthesis 2026',
  content: { type: 'doc', content: [] },
  contentText: 'Some valid markdown body for a proposed edit.',
  citations: [],
};

beforeEach(() => {
  mocks.selectRows = [];
  mocks.insertCaptured = [];
  mocks.updateCaptured = [];
  mocks.returningResults = [];
  mocks.executeRows = [];
  mocks.txTouched = false;
});

describe('insertProposedEdit', () => {
  it('rejects an oversize title', async () => {
    await expect(
      insertProposedEdit({ ...baseInput, title: 'x'.repeat(501) }, 'user_x'),
    ).rejects.toThrow(/title must be 1-500/);
  });

  it('rejects an empty contentText', async () => {
    await expect(
      insertProposedEdit({ ...baseInput, contentText: '' }, 'user_x'),
    ).rejects.toThrow(/contentText/);
  });

  it('rejects an oversize contentText', async () => {
    await expect(
      insertProposedEdit({ ...baseInput, contentText: 'x'.repeat(500_001) }, 'user_x'),
    ).rejects.toThrow(/contentText/);
  });

  it('inserts with previousId=null when no pending row exists', async () => {
    mocks.selectRows = []; // no existing pending
    mocks.returningResults = [{ id: 'fresh-id' }];
    const r = await insertProposedEdit(baseInput, 'user_alice');
    expect(r.id).toBe('fresh-id');
    expect(r.supersededId).toBeNull();
    expect(mocks.updateCaptured).toHaveLength(0); // no supersede write
    const vals = mocks.insertCaptured[0] as Record<string, unknown>;
    expect(vals.previousId).toBeNull();
    expect(vals.proposedBy).toBe('user_alice');
  });

  it('supersedes the existing pending row and links previousId to it', async () => {
    mocks.selectRows = [{ id: 'old-pending-id' }];
    mocks.returningResults = [{ id: 'new-id' }];
    const r = await insertProposedEdit(baseInput, 'user_alice');
    expect(r.id).toBe('new-id');
    expect(r.supersededId).toBe('old-pending-id');
    // First write: update old row to superseded.
    expect(mocks.updateCaptured).toHaveLength(1);
    expect(mocks.updateCaptured[0].set.status).toBe('superseded');
    // Second write: insert the new row with previousId pointing to the old.
    const vals = mocks.insertCaptured[0] as Record<string, unknown>;
    expect(vals.previousId).toBe('old-pending-id');
  });
});

describe('getProposedEdit', () => {
  it('returns null when not found', async () => {
    mocks.selectRows = [];
    expect(await getProposedEdit('11111111-1111-1111-1111-111111111111')).toBeNull();
  });

  it('returns the row when found', async () => {
    const row = { id: 'p1', slug: 'aspirin-synthesis-2026', status: 'pending' };
    mocks.selectRows = [row];
    const result = await getProposedEdit('p1');
    expect(result).toEqual(row);
  });
});

describe('listPendingProposedEdits', () => {
  it('passes through the DB result list', async () => {
    mocks.selectRows = [{ id: 'p1' }, { id: 'p2' }];
    const rows = await listPendingProposedEdits();
    expect(rows.map((r) => r.id)).toEqual(['p1', 'p2']);
  });
});

describe('markProposedEditApplied', () => {
  it('reports found:false when the update touches no rows', async () => {
    mocks.returningResults = [];
    const r = await markProposedEditApplied('p1', 'admin_user', 'page-id-1', 'looks good');
    expect(r.found).toBe(false);
  });

  it('reports found:true when the row updates', async () => {
    mocks.returningResults = [{ id: 'p1' }];
    const r = await markProposedEditApplied('p1', 'admin_user', 'page-id-1', 'looks good');
    expect(r.found).toBe(true);
    expect(mocks.updateCaptured[0].set.status).toBe('applied');
    expect(mocks.updateCaptured[0].set.appliedPageId).toBe('page-id-1');
  });
});

describe('markProposedEditRejected', () => {
  it('rejects empty comment without hitting the DB', async () => {
    await expect(markProposedEditRejected('p1', 'admin_user', '')).rejects.toThrow(/1-2000/);
    expect(mocks.updateCaptured).toHaveLength(0);
  });

  it('rejects oversize comment', async () => {
    await expect(
      markProposedEditRejected('p1', 'admin_user', 'x'.repeat(2001)),
    ).rejects.toThrow(/1-2000/);
  });

  it('writes status=rejected + reviewer + comment on success', async () => {
    mocks.returningResults = [{ id: 'p1' }];
    const r = await markProposedEditRejected('p1', 'admin_user', 'duplicate of #42');
    expect(r.found).toBe(true);
    expect(mocks.updateCaptured[0].set).toMatchObject({
      status: 'rejected',
      reviewedBy: 'admin_user',
      reviewComment: 'duplicate of #42',
    });
  });
});

describe('tryClaimProposedEditForApply (Wave-3h TOCTOU fix)', () => {
  it('returns null when the proposal is no longer pending', async () => {
    // Empty executeRows → SELECT FOR UPDATE returns []. Helper returns null.
    const r = await tryClaimProposedEditForApply('p1', 'admin_user');
    expect(r).toBeNull();
    // Must NOT have issued any UPDATE.
    expect(mocks.updateCaptured).toHaveLength(0);
    // Must have opened a transaction (the SELECT FOR UPDATE needs row lock).
    expect(mocks.txTouched).toBe(true);
  });

  it('claims the proposal atomically and marks it applied', async () => {
    const row = {
      id: 'p1',
      slug: 'aspirin',
      title: 'Aspirin',
      contentText: 'body',
      status: 'pending',
    };
    mocks.executeRows.push([row]);
    const r = await tryClaimProposedEditForApply('p1', 'admin_user', 'looks good');
    expect(r).toEqual(row);
    expect(mocks.txTouched).toBe(true);
    // Exactly one UPDATE inside the transaction setting status=applied.
    expect(mocks.updateCaptured).toHaveLength(1);
    expect(mocks.updateCaptured[0].set).toMatchObject({
      status: 'applied',
      reviewedBy: 'admin_user',
      reviewComment: 'looks good',
    });
    // appliedPageId is NOT set yet — the caller fills it via setAppliedPageId.
    expect(mocks.updateCaptured[0].set.appliedPageId).toBeUndefined();
  });

  it('does NOT update when the row is gone (terminate cleanly)', async () => {
    mocks.executeRows.push([]); // explicit empty
    const r = await tryClaimProposedEditForApply('p1', 'admin_user');
    expect(r).toBeNull();
    expect(mocks.updateCaptured).toHaveLength(0);
  });
});

describe('setAppliedPageId / rollbackApplyClaim', () => {
  it('setAppliedPageId issues an UPDATE with appliedPageId', async () => {
    await setAppliedPageId('p1', 'page-id-1');
    expect(mocks.updateCaptured).toHaveLength(1);
    expect(mocks.updateCaptured[0].set).toEqual({ appliedPageId: 'page-id-1' });
  });

  it('rollbackApplyClaim resets status + clears reviewer fields', async () => {
    await rollbackApplyClaim('p1');
    expect(mocks.updateCaptured).toHaveLength(1);
    expect(mocks.updateCaptured[0].set).toMatchObject({
      status: 'pending',
      reviewedBy: null,
      reviewedAt: null,
      reviewComment: null,
    });
  });
});

describe('countPendingProposedEdits (Wave-3h perf — cheap nav badge)', () => {
  it('returns the COUNT(*) result', async () => {
    mocks.executeRows.push([{ count: 7 }]);
    expect(await countPendingProposedEdits()).toBe(7);
  });

  it('coerces BIGINT-string Postgres values to Number', async () => {
    mocks.executeRows.push([{ count: '42' }]);
    expect(await countPendingProposedEdits()).toBe(42);
  });

  it('returns 0 when the result is empty', async () => {
    expect(await countPendingProposedEdits()).toBe(0);
  });
});
