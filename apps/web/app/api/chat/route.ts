import { NextRequest, NextResponse } from 'next/server';
import { auth } from '@/lib/auth';
import { buildQueryOptions } from '@/lib/agent';
import { agentToStream } from '@/lib/streaming';
import { randomUUID } from 'crypto';

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
  const sessionId = typeof body.sessionId === 'string' ? body.sessionId : randomUUID();

  if (typeof prompt !== 'string' || prompt.trim() === '') {
    return NextResponse.json({ error: 'prompt is required' }, { status: 400 });
  }

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
