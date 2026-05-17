import { NextResponse } from 'next/server';
import { approveCampaignStep } from '@chemclaw2/db';
import { withRouteParams, errorResponse } from '@/lib/api-gate';
import { UUID_RE } from '@/lib/validation';

/**
 * Release a single per-step approval gate. Owner-scoped: the user must have
 * created the campaign. Flips requires_approval=false; the worker poll picks
 * the step up on its next sweep (or sooner via next_retry_at).
 */
export const POST = withRouteParams<{ id: string; idx: string }>(
  { rateLimit: { key: 'campaign', max: 30, windowMs: 60_000 } },
  async ({ userId, params }) => {
    if (!UUID_RE.test(params.id)) return errorResponse('invalid campaign id', 400);
    const stepIdx = Number(params.idx);
    if (!Number.isInteger(stepIdx) || stepIdx < 0) return errorResponse('invalid step index', 400);

    const { approved } = await approveCampaignStep(params.id, userId, stepIdx);
    if (!approved) {
      return errorResponse(
        'Step not found, not owned by you, already approved, or no longer pending',
        404,
      );
    }
    return NextResponse.json({ approved: true, stepIdx });
  },
);
