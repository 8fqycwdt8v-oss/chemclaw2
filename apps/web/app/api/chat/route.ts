import { z } from 'zod';
import { NextRequest, NextResponse } from 'next/server';
import { buildQueryOptions } from '@/lib/agent';
import { agentToStream } from '@/lib/streaming';
import { withApiContext } from '@/lib/api-context';
import { scheduledSubstanceGate, MAX_PROMPT_BYTES } from '@chemclaw2/agent-tools';
import { recordOverride, getProjectBudget, incrementSpend } from '@chemclaw2/db';
import { logger } from '@chemclaw2/observability';
import { randomUUID } from 'crypto';
import { requireUserWithRateLimit } from '@/lib/api-gate';

const MAX_JUSTIFICATION_LEN = 2000;
const RATE_LIMIT_REQUESTS = 20;
const RATE_LIMIT_WINDOW_MS = 60_000;

const BodySchema = z.object({
  prompt: z.string().trim().min(1).refine(
    (s) => Buffer.byteLength(s, 'utf8') <= MAX_PROMPT_BYTES,
    { message: 'prompt too large' },
  ),
  sessionId: z.string().uuid().optional().catch(undefined),
  override_justification: z.string().min(20).max(MAX_JUSTIFICATION_LEN).optional().catch(undefined),
  plan_mode: z.boolean().optional().catch(undefined),
});

export async function POST(req: NextRequest) {
  return withApiContext(async () => {
    const apiGate = await requireUserWithRateLimit(
      'chat', RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_MS,
      'Too many requests — please wait before sending another message',
    );
    if (apiGate instanceof NextResponse) return apiGate;
    const { userId } = apiGate;

    let raw: unknown;
    try {
      raw = await req.json();
    } catch (err) {
      logger.warn('json_parse_failed', { route: 'chat' }, err);
      return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
    }

    const parsed = BodySchema.safeParse(raw);
    if (!parsed.success) {
      const first = parsed.error.issues[0];
      const isSize = first?.message === 'prompt too large';
      logger.info('validation_rejected', { route: 'chat', reason: first?.message ?? 'unknown', oversize: isSize });
      return NextResponse.json(
        { error: first?.message ?? 'invalid request body' },
        { status: isSize ? 413 : 400 },
      );
    }
    const body = parsed.data;

    const prompt = body.prompt;
    const sessionId = body.sessionId ?? randomUUID();

    const gate = scheduledSubstanceGate(prompt);
    if (gate.blocked) {
      const justification = body.override_justification?.trim();
      if (!justification) {
        return NextResponse.json({
          error: gate.reason,
          override_available: true,
          override_hint: 'Provide override_justification (20-2000 chars) to proceed with this request.',
        }, { status: 403 });
      }
      try {
        await recordOverride(sessionId, userId, 'scheduled_substance', justification, prompt);
      } catch (err) {
        logger.error('record_override_failed', { session_id: sessionId, user_id: userId }, err);
        throw err;
      }
    }

    const planMode = body.plan_mode === true;
    const options = buildQueryOptions(sessionId, userId, { planMode });
    const projectKey = `chemclaw2:${userId}`;
    const stream = agentToStream(prompt, options, {
      async onResult(result) {
        const tokens = result.inputTokens + result.outputTokens;
        if (tokens === 0) return;
        const budget = await getProjectBudget(projectKey).catch((err) => {
          logger.error('get_project_budget_failed', { session_id: sessionId, user_id: userId, project_key: projectKey, tokens }, err);
          return null;
        });
        if (!budget) return;
        await incrementSpend(projectKey, budget.period, { tokens }).catch((err) => {
          logger.error('increment_spend_tokens_failed', { session_id: sessionId, user_id: userId, project_key: projectKey, period: budget.period, tokens }, err);
        });
      },
    });

    return new Response(stream, {
      headers: {
        'Content-Type': 'text/event-stream',
        'Cache-Control': 'no-cache, no-store',
        Connection: 'keep-alive',
        'X-Accel-Buffering': 'no',
      },
    });
  });
}
