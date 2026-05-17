import { z } from 'zod';
import { NextResponse } from 'next/server';
import {
  getProjectBudget,
  upsertProjectBudget,
  getCurrentSpend,
  type BudgetPeriod,
} from '@chemclaw2/db';
import { withRouteParams, errorResponse } from '@/lib/api-gate';
import { PROJECT_KEY_RE } from '@chemclaw2/agent-tools';

const Cap = z.union([z.literal(null), z.number().int().nonnegative()]);

const BudgetPutBody = z.object({
  period: z.enum(['day', 'week', 'month'], { message: 'period must be day|week|month' }),
  toolCallsCap: Cap.optional(),
  experimentsCap: Cap.optional(),
  tokensCap: Cap.optional(),
});

export const GET = withRouteParams<{ projectKey: string }>(
  { auth: 'admin' },
  async ({ params }) => {
    if (!PROJECT_KEY_RE.test(params.projectKey)) return errorResponse('invalid projectKey', 400);
    const budget = await getProjectBudget(params.projectKey);
    if (!budget) return NextResponse.json({ projectKey: params.projectKey, budget: null, spend: null });
    const spend = await getCurrentSpend(params.projectKey, budget.period);
    return NextResponse.json({ projectKey: params.projectKey, budget, spend });
  },
);

export const PUT = withRouteParams<{ projectKey: string }, typeof BudgetPutBody>(
  { auth: 'admin', body: BudgetPutBody },
  async ({ userId, params, body }) => {
    if (!PROJECT_KEY_RE.test(params.projectKey)) {
      return errorResponse('projectKey must match /^[A-Za-z0-9:_-]{1,64}$/', 400);
    }
    await upsertProjectBudget(
      params.projectKey,
      body.period as BudgetPeriod,
      {
        toolCallsCap: body.toolCallsCap ?? null,
        experimentsCap: body.experimentsCap ?? null,
        tokensCap: body.tokensCap ?? null,
      },
      userId,
    );
    return NextResponse.json({ ok: true });
  },
);
