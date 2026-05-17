import { UUID_RE } from '@/lib/validation';
import { NextRequest, NextResponse } from 'next/server';
import { buildQueryOptions } from '@/lib/agent';
import { agentToStream } from '@/lib/streaming';
import { scheduledSubstanceGate, MAX_PROMPT_BYTES } from '@chemclaw2/agent-tools';
import { recordOverride, getProjectBudget, incrementSpend } from '@chemclaw2/db';
import { randomUUID } from 'crypto';
import { requireUserWithRateLimit } from '@/lib/api-gate';

const MAX_JUSTIFICATION_LEN = 2000;
const RATE_LIMIT_REQUESTS = 20;
const RATE_LIMIT_WINDOW_MS = 60_000;

export async function POST(req: NextRequest) {
  const apiGate = await requireUserWithRateLimit(
    'chat', RATE_LIMIT_REQUESTS, RATE_LIMIT_WINDOW_MS,
    'Too many requests — please wait before sending another message',
  );
  if (apiGate instanceof NextResponse) return apiGate;
  const { userId } = apiGate;

  let body: {
    prompt?: unknown;
    sessionId?: unknown;
    override_justification?: unknown;
    plan_mode?: unknown;
  };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
  }

  const prompt = body.prompt;
  if (typeof prompt !== 'string' || prompt.trim() === '') {
    return NextResponse.json({ error: 'prompt is required' }, { status: 400 });
  }
  if (Buffer.byteLength(prompt, 'utf8') > MAX_PROMPT_BYTES) {
    return NextResponse.json({ error: 'prompt too large' }, { status: 413 });
  }

  // Validate client-supplied sessionId to prevent header injection; fall back to fresh UUID
  const sessionId =
    typeof body.sessionId === 'string' && UUID_RE.test(body.sessionId)
      ? body.sessionId
      : randomUUID();

  // Scheduled-substance gate: blocks by default. An authenticated user may
  // supply override_justification (≥20 chars) to bypass — the justification
  // and a prompt hash are recorded BEFORE the agent runs (append-only).
  const gate = scheduledSubstanceGate(prompt);
  if (gate.blocked) {
    const justification = typeof body.override_justification === 'string'
      ? body.override_justification.trim()
      : '';
    if (justification.length < 20 || justification.length > MAX_JUSTIFICATION_LEN) {
      return NextResponse.json({
        error: gate.reason,
        override_available: true,
        override_hint: 'Provide override_justification (20-2000 chars) to proceed with this request.',
      }, { status: 403 });
    }
    await recordOverride(sessionId, userId, 'scheduled_substance', justification, prompt);
  }

  const planMode = body.plan_mode === true;
  const options = buildQueryOptions(sessionId, userId, { planMode });
  // Wave-2c opportunity #6: persist LLM input+output tokens to the period
  // spend row at end-of-stream. Cache-read/create tokens are reported but
  // not billed against tokens_cap (they're effectively free and would
  // punish cache-friendly prompts). Failure is logged but never blocks
  // the response — same fail-open semantics as the tool-call budget hook.
  const projectKey = `chemclaw2:${userId}`;
  const stream = agentToStream(prompt.trim(), options, {
    async onResult(result) {
      const tokens = result.inputTokens + result.outputTokens;
      if (tokens === 0) return;
      const budget = await getProjectBudget(projectKey).catch((err) => {
        console.error('[chat] getProjectBudget failed:', err);
        return null;
      });
      if (!budget) return;
      await incrementSpend(projectKey, budget.period, { tokens }).catch((err) => {
        console.error('[chat] incrementSpend(tokens) failed:', err);
      });
    },
  });

  return new Response(stream, {
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache, no-store',
      Connection: 'keep-alive',
      // Followup #11: tells nginx-style proxies (incl. Fly's) not to buffer
      // chunks; without this the live-progress UX can stall multiple seconds.
      'X-Accel-Buffering': 'no',
    },
  });
}
