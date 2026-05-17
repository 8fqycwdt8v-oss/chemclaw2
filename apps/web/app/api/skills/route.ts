import { UUID_RE } from '@/lib/validation';
import { auth } from '@clerk/nextjs/server';
import { NextResponse } from 'next/server';
import { writeFile, mkdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { replaySession } from '@chemclaw2/db';
import { rateLimit } from '@/lib/rate-limit';
import { withApiContext } from '@/lib/api-context';
import { logger } from '@chemclaw2/observability';

const SKILLS_DIR =
  process.env.SKILLS_DIR ?? join(process.cwd(), '..', '..', '.claude', 'skills');
const SKILL_NAME_RE = /^[a-z][a-z0-9-]{1,40}$/;

/**
 * Capture the last successful turn from a session and persist it as a new skill
 * pack on disk.
 */
export async function POST(req: Request) {
  return withApiContext(async () => {
    const { userId } = await auth();
    if (!userId) {
      logger.info('auth_denied', { route: 'skills' });
      return NextResponse.json({ error: 'Unauthorized' }, { status: 401 });
    }

    const { limited } = await rateLimit(`skills:${userId}`, 10, 60_000);
    if (limited) {
      logger.warn('rate_limit_hit', { route: 'skills', user_id: userId });
      return NextResponse.json({ error: 'Too many requests' }, { status: 429 });
    }

    let body: { sessionId?: unknown; name?: unknown; description?: unknown };
    try {
      body = (await req.json()) as typeof body;
    } catch (err) {
      logger.warn('json_parse_failed', { route: 'skills' }, err);
      return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
    }
    if (typeof body.sessionId !== 'string' || !UUID_RE.test(body.sessionId)) {
      logger.info('validation_rejected', { route: 'skills', field: 'sessionId', reason: 'shape' });
      return NextResponse.json({ error: 'sessionId must be a UUID' }, { status: 400 });
    }
    if (typeof body.name !== 'string' || !SKILL_NAME_RE.test(body.name)) {
      logger.info('validation_rejected', { route: 'skills', field: 'name', reason: 'shape' });
      return NextResponse.json({ error: 'name must be lowercase kebab-case (2-40 chars)' }, { status: 400 });
    }
    if (typeof body.description !== 'string' || body.description.length === 0 || body.description.length > 500) {
      logger.info('validation_rejected', { route: 'skills', field: 'description', reason: 'shape' });
      return NextResponse.json({ error: 'description (1-500 chars) is required' }, { status: 400 });
    }

    const entries = await replaySession(body.sessionId, `chemclaw2:${userId}`).catch((err) => {
      logger.error('replay_session_failed', { session_id: body.sessionId as string, user_id: userId }, err);
      throw err;
    });
    if (entries.length === 0) {
      return NextResponse.json({ error: 'session has no entries' }, { status: 404 });
    }
    // Trim to last user→assistant pair (the "successful turn" being saved).
    const trimmed = entries.slice(-4);

    const dir = join(SKILLS_DIR, body.name);
    if (existsSync(dir)) {
      logger.info('skill_name_collision', { name: body.name, user_id: userId });
      return NextResponse.json({ error: 'skill name already exists' }, { status: 409 });
    }
    await mkdir(dir, { recursive: true }).catch((err) => {
      logger.error('skill_mkdir_failed', { dir, user_id: userId }, err);
      throw err;
    });

    const yamlDesc = body.description.replace(/\s+/g, ' ').trim().replace(/'/g, "''");
    const md = [
      '---',
      `name: ${body.name}`,
      `description: '${yamlDesc}'`,
      '---',
      '',
      `# ${body.name}`,
      '',
      body.description,
      '',
      '## Captured from a successful turn',
      '',
      `_Authored by ${userId} from session ${body.sessionId}_`,
      '',
      '```json',
      JSON.stringify(trimmed, null, 2),
      '```',
    ].join('\n');
    await writeFile(join(dir, 'SKILL.md'), md, 'utf8').catch((err) => {
      logger.error('skill_write_failed', { path: join(dir, 'SKILL.md'), user_id: userId }, err);
      throw err;
    });

    logger.info('skill_saved', { name: body.name, user_id: userId, session_id: body.sessionId as string });
    return NextResponse.json({
      name: body.name,
      path: join('.claude', 'skills', body.name, 'SKILL.md'),
      note: 'Skill saved to container filesystem. Commit to the repo via PR to persist.',
    });
  });
}
