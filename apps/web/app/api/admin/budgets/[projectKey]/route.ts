import { NextResponse } from 'next/server';
import {
  getProjectBudget,
  upsertProjectBudget,
  getCurrentSpend,
  type BudgetPeriod,
} from '@chemclaw2/db';
import { requireAdminApi } from '@/lib/auth';
import { withApiContext } from '@/lib/api-context';
import { logger } from '@chemclaw2/observability';
import { PROJECT_KEY_RE } from '@chemclaw2/agent-tools';

/**
 * Per-project budget caps. Admin-only (Clerk publicMetadata.role='admin').
 * No standalone UI in v2.1 — operators curl this route, matching the
 * tool-permissions admin pattern.
 *
 * GET  → current cap + current-period spend
 * PUT  → upsert cap (period, toolCallsCap, experimentsCap)
 */

export async function GET(_req: Request, { params }: { params: Promise<{ projectKey: string }> }) {
  return withApiContext(async () => {
    const gate = await requireAdminApi();
    if (gate instanceof NextResponse) return gate;

    const { projectKey } = await params;
    if (!PROJECT_KEY_RE.test(projectKey)) {
      logger.info('validation_rejected', { route: 'admin_budgets', field: 'projectKey', reason: 'shape' });
      return NextResponse.json({ error: 'invalid projectKey' }, { status: 400 });
    }
    const budget = await getProjectBudget(projectKey).catch((err) => {
      logger.error('get_project_budget_failed', { project_key: projectKey }, err);
      throw err;
    });
    if (!budget) return NextResponse.json({ projectKey, budget: null, spend: null });

    const spend = await getCurrentSpend(projectKey, budget.period).catch((err) => {
      logger.error('get_current_spend_failed', { project_key: projectKey, period: budget.period }, err);
      throw err;
    });
    return NextResponse.json({ projectKey, budget, spend });
  });
}

export async function PUT(req: Request, { params }: { params: Promise<{ projectKey: string }> }) {
  return withApiContext(async () => {
    const gate = await requireAdminApi();
    if (gate instanceof NextResponse) return gate;
    const { userId: adminUserId } = gate;

    const { projectKey } = await params;
    if (!PROJECT_KEY_RE.test(projectKey)) {
      logger.info('validation_rejected', { route: 'admin_budgets', field: 'projectKey', reason: 'shape' });
      return NextResponse.json(
        { error: 'projectKey must match /^[A-Za-z0-9:_-]{1,64}$/' },
        { status: 400 },
      );
    }

    let body: {
      period?: unknown;
      toolCallsCap?: unknown;
      experimentsCap?: unknown;
      tokensCap?: unknown;
    };
    try {
      body = (await req.json()) as typeof body;
    } catch (err) {
      logger.warn('json_parse_failed', { route: 'admin_budgets' }, err);
      return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
    }

    if (body.period !== 'day' && body.period !== 'week' && body.period !== 'month') {
      logger.info('validation_rejected', { route: 'admin_budgets', field: 'period', reason: 'enum' });
      return NextResponse.json({ error: 'period must be day|week|month' }, { status: 400 });
    }
    const validateCap = (v: unknown, name: string): number | null | NextResponse => {
      if (v === null || v === undefined) return null;
      if (typeof v === 'number' && Number.isInteger(v) && v >= 0) return v;
      logger.info('validation_rejected', { route: 'admin_budgets', field: name, reason: 'shape' });
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
    ).catch((err) => {
      logger.error('upsert_project_budget_failed', { project_key: projectKey, period: body.period, admin_id: adminUserId }, err);
      throw err;
    });
    logger.info('budget_updated', { project_key: projectKey, period: body.period, admin_id: adminUserId });
    return NextResponse.json({ ok: true });
  });
}
