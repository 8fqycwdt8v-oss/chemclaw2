import { z } from 'zod';
import { buildQueryOptions } from '@/lib/agent';
import { agentToStream } from '@/lib/streaming';
import { scheduledSubstanceGate, MAX_PROMPT_BYTES } from '@chemclaw2/agent-tools';
import { recordOverride, getProjectBudget, incrementSpend } from '@chemclaw2/db';
import { randomUUID } from 'crypto';
import { withRoute, errorResponse } from '@/lib/api-gate';

const MAX_JUSTIFICATION_LEN = 2000;
const RATE_LIMIT_REQUESTS = 20;
const RATE_LIMIT_WINDOW_MS = 60_000;

// `.catch(undefined)` on optional fields keeps a malformed but non-mandatory
// field from 400'ing the whole request.
const BodySchema = z.object({
  prompt: z.string().trim().min(1).refine(
    (s) => Buffer.byteLength(s, 'utf8') <= MAX_PROMPT_BYTES,
    { message: 'prompt too large' },
  ),
  sessionId: z.string().uuid().optional().catch(undefined),
  override_justification: z.string().min(20).max(MAX_JUSTIFICATION_LEN).optional().catch(undefined),
  plan_mode: z.boolean().optional().catch(undefined),
});

export const POST = withRoute(
  {
    rateLimit: {
      key: 'chat',
      max: RATE_LIMIT_REQUESTS,
      windowMs: RATE_LIMIT_WINDOW_MS,
      message: 'Too many requests — please wait before sending another message',
    },
    body: BodySchema,
  },
  async ({ userId, body }) => {
    const prompt = body.prompt;
    const sessionId = body.sessionId ?? randomUUID();

    // Scheduled-substance gate: blocks by default. The user may supply
    // override_justification (≥20 chars) to bypass; justification + prompt
    // hash are recorded BEFORE the agent runs (append-only).
    const gate = scheduledSubstanceGate(prompt);
    if (gate.blocked) {
      const justification = body.override_justification?.trim();
      if (!justification) {
        return errorResponse(gate.reason ?? 'Request blocked by scheduled-substance gate', 403, {
          override_available: true,
          override_hint:
            'Provide override_justification (20-2000 chars) to proceed with this request.',
        });
      }
      await recordOverride(sessionId, userId, 'scheduled_substance', justification, prompt);
    }

    const planMode = body.plan_mode === true;
    const options = buildQueryOptions(sessionId, userId, { planMode });
    const projectKey = `chemclaw2:${userId}`;
    const stream = agentToStream(prompt, options, {
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
        'X-Accel-Buffering': 'no',
      },
    });
  },
);
