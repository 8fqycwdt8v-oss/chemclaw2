import { readdirSync, readFileSync, existsSync } from 'node:fs';
import { join } from 'node:path';

const SKILLS_DIR = process.env.SKILLS_DIR ?? join(process.cwd(), '..', '..', 'skills');

/**
 * Load all skill packs from disk on first call (then cache). Each skill is a
 * directory under `skills/` containing a `skill.md` file. The full body of
 * every skill.md is concatenated into a single Skills section that gets
 * appended to the system prompt.
 *
 * Returns an empty string when the directory is absent — keeps the agent
 * functional in environments where skills aren't shipped (e.g. test).
 */
let cached: string | null = null;

export function loadSkillsBlock(): string {
  if (cached !== null) return cached;
  if (!existsSync(SKILLS_DIR)) {
    cached = '';
    return cached;
  }
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
  cached = parts.length === 0 ? '' : '\n\n## Skills available\n\n' + parts.join('\n\n---\n\n');
  return cached;
}
