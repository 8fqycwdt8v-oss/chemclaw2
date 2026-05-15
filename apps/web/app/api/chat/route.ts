import { NextRequest, NextResponse } from 'next/server';
import { auth } from '@/lib/auth';
import { buildQueryOptions } from '@/lib/agent';
import { agentToStream } from '@/lib/streaming';
import { scheduledSubstanceGate } from '@chemclaw2/agent-tools';
import { randomUUID } from 'crypto';

const UUID_RE = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_PROMPT_BYTES = 32_768;

export async function POST(req: NextRequest) {
  const { userId } = await auth();
  if (!userId) {
    return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
  }

  let body: { prompt?: unknown; sessionId?: unknown };
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

  // Safety: block prompts containing scheduled substance synthesis terms
  const gate = scheduledSubstanceGate(prompt);
  if (gate.blocked) {
    return NextResponse.json({ error: gate.reason }, { status: 400 });
  }

  // Validate client-supplied sessionId to prevent header injection; fall back to fresh UUID
  const sessionId =
    typeof body.sessionId === 'string' && UUID_RE.test(body.sessionId)
      ? body.sessionId
      : randomUUID();

  const options = buildQueryOptions(sessionId, userId);
  const stream = agentToStream(prompt.trim(), options);

  return new Response(stream, {
    headers: {
      'Content-Type': 'text/event-stream',
      'Cache-Control': 'no-cache, no-store',
      Connection: 'keep-alive',
      'X-Session-Id': sessionId,
    },
  });
}
