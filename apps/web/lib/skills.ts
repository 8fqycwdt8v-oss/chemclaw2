import { readdirSync, readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';

const SKILLS_DIR = process.env.SKILLS_DIR ?? join(process.cwd(), '..', '..', 'skills');

/**
 * Load all skill packs from disk. Each skill is a directory under `skills/`
 * containing a `skill.md` file. The full body of every skill.md is
 * concatenated into a single Skills section that gets appended to the system
 * prompt.
 *
 * Returns an empty string when the directory is absent — keeps the agent
 * functional in test environments where skills aren't shipped.
 *
 * Followup #10: was cached for the lifetime of the Node process; saved
 * skills (POST /api/skills writes a new file) became invisible until image
 * rebuild. Disk I/O per chat turn is a handful of files — fine for the
 * expected traffic load. Drop the cache.
 */
export function loadSkillsBlock(): string {
  if (!existsSync(SKILLS_DIR)) return '';
  const entries = readdirSync(SKILLS_DIR, { withFileTypes: true })
    .filter((e) => e.isDirectory())
    .sort((a, b) => a.name.localeCompare(b.name));
  const parts: string[] = [];
  for (const e of entries) {
    const path = join(SKILLS_DIR, e.name, 'skill.md');
    if (!existsSync(path)) continue;
    try {
      parts.push(readFileSync(path, 'utf8').trim());
    } catch {
      // Skip individual unreadable skills; an operator should notice via logs.
    }
  }
  return parts.length === 0 ? '' : '\n\n## Skills available\n\n' + parts.join('\n\n---\n\n');
}
