import { describe, it, expect, vi, beforeEach } from 'vitest';

const mocks = vi.hoisted(() => ({
  insertReturning: [] as Array<Array<{ id: string }>>,
  selectReturning: [] as Array<Array<{ id: string }>>,
  updates: [] as Array<{ set: Record<string, unknown> }>,
}));

vi.mock('../client', () => ({
  db: {
    insert: () => ({
      values: () => ({
        onConflictDoNothing: () => ({
          returning: () => Promise.resolve(mocks.insertReturning.shift() ?? []),
        }),
        returning: () => Promise.resolve(mocks.insertReturning.shift() ?? []),
      }),
    }),
    select: () => ({
      from: () => ({
        where: () => Promise.resolve(mocks.selectReturning.shift() ?? []),
      }),
    }),
    update: () => ({
      set: (set: Record<string, unknown>) => {
        mocks.updates.push({ set });
        return { where: () => Promise.resolve() };
      },
    }),
  },
}));

import { addCampaignStep, markStepFailed } from '../queries/campaigns';

beforeEach(() => {
  mocks.insertReturning.length = 0;
  mocks.selectReturning.length = 0;
  mocks.updates.length = 0;
});

describe('addCampaignStep (idempotent insert via UNIQUE(campaign_id, step_idx))', () => {
  it('returns the newly-inserted id on a fresh (campaign_id, step_idx) pair', async () => {
    mocks.insertReturning.push([{ id: 'new-step-id' }]);
    const id = await addCampaignStep('camp-uuid', 0, { reactionSmiles: 'CC>>CCO' });
    expect(id).toBe('new-step-id');
  });

  it('on conflict (insert returns no row), reads back the existing row id', async () => {
    mocks.insertReturning.push([]); // onConflictDoNothing → no row
    mocks.selectReturning.push([{ id: 'existing-step-id' }]);
    const id = await addCampaignStep('camp-uuid', 0);
    expect(id).toBe('existing-step-id');
  });

  it('throws when both the conflict insert and the read-back miss (unexpected state)', async () => {
    mocks.insertReturning.push([]);
    mocks.selectReturning.push([]);
    await expect(addCampaignStep('camp-uuid', 0)).rejects.toThrow(/insert no-op but row not found/);
  });
});

describe('markStepFailed retry clamp', () => {
  it('writes retry_count = 1 when previous count was 0', async () => {
    await markStepFailed('step-id', 0);
    expect(mocks.updates[0].set.retryCount).toBe(1);
  });

  it('clamps an absurd corrupt input to the CHECK boundary (max written = 10)', async () => {
    await markStepFailed('step-id', 999);
    expect(mocks.updates[0].set.retryCount).toBe(10);
  });

  it('treats negative input as 0', async () => {
    await markStepFailed('step-id', -7);
    expect(mocks.updates[0].set.retryCount).toBe(1);
  });
});
