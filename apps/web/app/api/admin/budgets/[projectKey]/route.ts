import { auth, currentUser } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import {
  getProjectBudget,
  upsertProjectBudget,
  getCurrentSpend,
  type BudgetPeriod,
} from '@chemclaw2/db';

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
async function requireAdmin(): Promise<NextResponse | string> {
  const { userId } = await auth();
  if (!userId) return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  const user = await currentUser();
  const role = (user?.publicMetadata as { role?: string } | undefined)?.role;
  if (role !== 'admin') return NextResponse.json({ error: 'Forbidden — admin role required' }, { status: 403 });
  return userId;
}

export async function GET(_req: Request, { params }: { params: Promise<{ projectKey: string }> }) {
  const adminOrErr = await requireAdmin();
  if (adminOrErr instanceof NextResponse) return adminOrErr;

  const { projectKey } = await params;
  const budget = await getProjectBudget(projectKey);
  if (!budget) return NextResponse.json({ projectKey, budget: null, spend: null });

  const spend = await getCurrentSpend(projectKey, budget.period);
  return NextResponse.json({ projectKey, budget, spend });
}

export async function PUT(req: Request, { params }: { params: Promise<{ projectKey: string }> }) {
  const adminOrErr = await requireAdmin();
  if (adminOrErr instanceof NextResponse) return adminOrErr;
  const adminUserId = adminOrErr;

  const { projectKey } = await params;
  if (projectKey.length === 0 || projectKey.length > 200) {
    return NextResponse.json({ error: 'projectKey must be 1-200 chars' }, { status: 400 });
  }

  let body: { period?: unknown; toolCallsCap?: unknown; experimentsCap?: unknown };
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
  }

  if (body.period !== 'day' && body.period !== 'week' && body.period !== 'month') {
    return NextResponse.json({ error: 'period must be day|week|month' }, { status: 400 });
  }
  const toolCallsCap = body.toolCallsCap === null || body.toolCallsCap === undefined
    ? null
    : (typeof body.toolCallsCap === 'number' && Number.isInteger(body.toolCallsCap) && body.toolCallsCap >= 0
        ? body.toolCallsCap : 'invalid');
  if (toolCallsCap === 'invalid') {
    return NextResponse.json({ error: 'toolCallsCap must be a non-negative integer or null' }, { status: 400 });
  }
  const experimentsCap = body.experimentsCap === null || body.experimentsCap === undefined
    ? null
    : (typeof body.experimentsCap === 'number' && Number.isInteger(body.experimentsCap) && body.experimentsCap >= 0
        ? body.experimentsCap : 'invalid');
  if (experimentsCap === 'invalid') {
    return NextResponse.json({ error: 'experimentsCap must be a non-negative integer or null' }, { status: 400 });
  }

  await upsertProjectBudget(
    projectKey,
    body.period as BudgetPeriod,
    { toolCallsCap, experimentsCap },
    adminUserId,
  );
  return NextResponse.json({ ok: true });
}
