import { NextResponse } from 'next/server';
import {
  getProjectBudget,
  upsertProjectBudget,
  getCurrentSpend,
  type BudgetPeriod,
} from '@chemclaw2/db';
import { requireAdminApi } from '@/lib/auth';

/**
 * Per-project budget caps. Admin-only (Clerk publicMetadata.role='admin').
 * No standalone UI in v2.1 — operators curl this route, matching the
 * tool-permissions admin pattern.
 *
 * GET  → current cap + current-period spend
 * PUT  → upsert cap (period, toolCallsCap, experimentsCap)
 *
 * Body for PUT: {
 *   period: 'day'|'week'|'month',
 *   toolCallsCap?: number|null,
 *   experimentsCap?: number|null,
 * }
 */

export async function GET(_req: Request, { params }: { params: Promise<{ projectKey: string }> }) {
  const gate = await requireAdminApi();
  if (gate instanceof NextResponse) return gate;

  const { projectKey } = await params;
  const budget = await getProjectBudget(projectKey);
  if (!budget) return NextResponse.json({ projectKey, budget: null, spend: null });

  const spend = await getCurrentSpend(projectKey, budget.period);
  return NextResponse.json({ projectKey, budget, spend });
}

export async function PUT(req: Request, { params }: { params: Promise<{ projectKey: string }> }) {
  const gate = await requireAdminApi();
  if (gate instanceof NextResponse) return gate;
  const { userId: adminUserId } = gate;

  const { projectKey } = await params;
  if (projectKey.length === 0 || projectKey.length > 200) {
    return NextResponse.json({ error: 'projectKey must be 1-200 chars' }, { status: 400 });
  }

  let body: {
    period?: unknown;
    toolCallsCap?: unknown;
    experimentsCap?: unknown;
    tokensCap?: unknown;
  };
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
  }

  if (body.period !== 'day' && body.period !== 'week' && body.period !== 'month') {
    return NextResponse.json({ error: 'period must be day|week|month' }, { status: 400 });
  }
  const validateCap = (v: unknown, name: string): number | null | NextResponse => {
    if (v === null || v === undefined) return null;
    if (typeof v === 'number' && Number.isInteger(v) && v >= 0) return v;
    return NextResponse.json(
      { error: `${name} must be a non-negative integer or null` },
      { status: 400 },
    );
  };
  const toolCallsCap = validateCap(body.toolCallsCap, 'toolCallsCap');
  if (toolCallsCap instanceof NextResponse) return toolCallsCap;
  const experimentsCap = validateCap(body.experimentsCap, 'experimentsCap');
  if (experimentsCap instanceof NextResponse) return experimentsCap;
  const tokensCap = validateCap(body.tokensCap, 'tokensCap');
  if (tokensCap instanceof NextResponse) return tokensCap;

  await upsertProjectBudget(
    projectKey,
    body.period as BudgetPeriod,
    { toolCallsCap, experimentsCap, tokensCap },
    adminUserId,
  );
  return NextResponse.json({ ok: true });
}
