import { z } from 'zod';
import { NextResponse } from 'next/server';
import { writeFile, mkdir } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { join } from 'node:path';
import { replaySession } from '@chemclaw2/db';
import { withRoute, errorResponse } from '@/lib/api-gate';
import { UUID_RE } from '@/lib/validation';

const SKILLS_DIR =
  process.env.SKILLS_DIR ?? join(process.cwd(), '..', '..', '.claude', 'skills');
const SKILL_NAME_RE = /^[a-z][a-z0-9-]{1,40}$/;

const SkillsBody = z.object({
  sessionId: z.string().refine((s) => UUID_RE.test(s), 'sessionId must be a UUID'),
  name: z.string().refine((s) => SKILL_NAME_RE.test(s), 'name must be lowercase kebab-case (2-40 chars)'),
  description: z.string().min(1).max(500, 'description (1-500 chars) is required'),
});

/**
 * Capture the last successful turn from a session and persist it as a new skill
 * pack on disk. The file lands at .claude/skills/<name>/SKILL.md with YAML
 * frontmatter the Agent SDK uses for skill discovery; an operator commits it
 * to the repo via PR — that's the durability path.
 *
 * Admin-only: the Agent SDK auto-loads SKILL.md from the container
 * filesystem, so a skill written by user A becomes prompt context for every
 * subsequent session of every user (until the container is reclaimed). That
 * makes this endpoint a persistent prompt-injection sink unless gated.
 */
export const POST = withRoute(
  { auth: 'admin', rateLimit: { key: 'skills', max: 10, windowMs: 60_000 }, body: SkillsBody },
  async ({ userId, body }) => {
    const entries = await replaySession(body.sessionId, `chemclaw2:${userId}`);
    if (entries.length === 0) return errorResponse('session has no entries', 404);
    const trimmed = entries.slice(-4);

    const dir = join(SKILLS_DIR, body.name);
    if (existsSync(dir)) return errorResponse('skill name already exists', 409);
    await mkdir(dir, { recursive: true });

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
  },
);
