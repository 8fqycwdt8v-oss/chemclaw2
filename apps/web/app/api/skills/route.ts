import { UUID_RE } from '@/lib/validation';
import { NextResponse } from 'next/server';
import { writeFile, mkdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { replaySession } from '@chemclaw2/db';
import { requireUserWithRateLimit } from '@/lib/api-gate';

const SKILLS_DIR =
  process.env.SKILLS_DIR ?? join(process.cwd(), '..', '..', '.claude', 'skills');
const SKILL_NAME_RE = /^[a-z][a-z0-9-]{1,40}$/;

/**
 * Capture the last successful turn from a session and persist it as a new skill
 * pack on disk. The file lands at .claude/skills/<name>/SKILL.md with YAML
 * frontmatter the Agent SDK uses for skill discovery; an operator commits it
 * to the repo via PR — that's the durability path. This keeps the
 * implementation tiny (no dynamic skill registry, no DB table).
 */
export async function POST(req: Request) {
  const gate = await requireUserWithRateLimit('skills', 10, 60_000);
  if (gate instanceof NextResponse) return gate;
  const { userId } = gate;

  let body: { sessionId?: unknown; name?: unknown; description?: unknown };
  try {
    body = (await req.json()) as typeof body;
  } catch {
    return NextResponse.json({ error: 'Invalid JSON' }, { status: 400 });
  }
  if (typeof body.sessionId !== 'string' || !UUID_RE.test(body.sessionId)) {
    return NextResponse.json({ error: 'sessionId must be a UUID' }, { status: 400 });
  }
  if (typeof body.name !== 'string' || !SKILL_NAME_RE.test(body.name)) {
    return NextResponse.json({ error: 'name must be lowercase kebab-case (2-40 chars)' }, { status: 400 });
  }
  if (typeof body.description !== 'string' || body.description.length === 0 || body.description.length > 500) {
    return NextResponse.json({ error: 'description (1-500 chars) is required' }, { status: 400 });
  }

  const entries = await replaySession(body.sessionId, `chemclaw2:${userId}`);
  if (entries.length === 0) {
    return NextResponse.json({ error: 'session has no entries' }, { status: 404 });
  }
  // Trim to last user→assistant pair (the "successful turn" being saved).
  const trimmed = entries.slice(-4);

  const dir = join(SKILLS_DIR, body.name);
  if (existsSync(dir)) {
    return NextResponse.json({ error: 'skill name already exists' }, { status: 409 });
  }
  await mkdir(dir, { recursive: true });

  // YAML single-quoted scalar: collapse newlines, then double any embedded
  // single quotes. Plain scalars would break on ": " sequences or YAML-reserved
  // leading chars in user-supplied text — the SDK would silently skip such a
  // SKILL.md as unparseable.
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
  await writeFile(join(dir, 'SKILL.md'), md, 'utf8');

  return NextResponse.json({
    name: body.name,
    path: join('.claude', 'skills', body.name, 'SKILL.md'),
    note: 'Skill saved to container filesystem. Commit to the repo via PR to persist.',
  });
}
