/**
 * Minimal markdown → Tiptap JSON converter.
 *
 * The agent writes wiki bodies as markdown (deep-research, wiki_upsert).
 * The previous implementation wrapped raw markdown text in a single Tiptap
 * paragraph node, so the editor displayed `**bold**` and `## Header` literally
 * and human-authored Tiptap structure was destroyed on agent overwrite.
 *
 * This converter handles the subset that appears in agent output:
 *   - paragraphs (blank-line separated)
 *   - headers (#, ##, ### at line start — h4-h6 collapse to h3)
 *   - bullet lists (`-` or `*` at line start)
 *   - ordered lists (`1.`, `2.`, ... at line start)
 *   - blockquotes (`>` at line start)
 *   - fenced code blocks (```)
 *   - inline marks: bold (`**x**`), inline code (`` `x` ``)
 *
 * Italic (`*x*`) is intentionally NOT parsed: it collides with SMILES wildcard
 * atoms (e.g. `CC*CC*CCO` would render `CC<italic>CC</italic>CCO`). The cost
 * is that genuine markdown italic shows up with literal asterisks in the
 * editor — acceptable for a chemistry KB where italics are uncommon and
 * mis-parsed SMILES is a correctness issue.
 *
 * Things NOT handled (would silently flow through as plain text):
 *   - tables, images, links (kept as literal `[text](url)` for now)
 *   - headers below ### collapsed to ###
 *   - nested lists (treated as flat)
 *
 * Tiptap StarterKit covers all the emitted node types.
 */

type TiptapMark = { type: 'bold' | 'code' };

type TiptapTextNode = { type: 'text'; text: string; marks?: TiptapMark[] };

type TiptapBlock =
  | { type: 'paragraph'; content?: TiptapTextNode[] }
  | { type: 'heading'; attrs: { level: 1 | 2 | 3 }; content?: TiptapTextNode[] }
  | { type: 'bulletList'; content: TiptapBlock[] }
  | { type: 'orderedList'; content: TiptapBlock[] }
  | { type: 'listItem'; content: TiptapBlock[] }
  | { type: 'blockquote'; content: TiptapBlock[] }
  | { type: 'codeBlock'; content?: TiptapTextNode[] };

export type TiptapDoc = { type: 'doc'; content: TiptapBlock[] };

function tokenizeInline(text: string): TiptapTextNode[] {
  // Emit text nodes with marks for bold (`**x**`) and inline code (`` `x` ``).
  // Single `*` characters (SMILES wildcards) are passed through verbatim.
  const out: TiptapTextNode[] = [];
  let i = 0;
  const len = text.length;

  const pushPlain = (s: string) => {
    if (s.length === 0) return;
    const last = out[out.length - 1];
    if (last && (!last.marks || last.marks.length === 0)) {
      last.text += s;
    } else {
      out.push({ type: 'text', text: s });
    }
  };

  while (i < len) {
    // Inline code: `x`
    if (text[i] === '`') {
      const end = text.indexOf('`', i + 1);
      if (end !== -1) {
        const inner = text.slice(i + 1, end);
        if (inner.length > 0) out.push({ type: 'text', text: inner, marks: [{ type: 'code' }] });
        i = end + 1;
        continue;
      }
    }
    // Bold: **x**
    if (text[i] === '*' && text[i + 1] === '*') {
      const end = text.indexOf('**', i + 2);
      if (end !== -1) {
        const inner = text.slice(i + 2, end);
        if (inner.length > 0) {
          // Bold contents may themselves contain code marks.
          for (const child of tokenizeInline(inner)) {
            const marks: TiptapMark[] = [{ type: 'bold' }, ...(child.marks ?? [])];
            out.push({ type: 'text', text: child.text, marks });
          }
        }
        i = end + 2;
        continue;
      }
    }
    pushPlain(text[i]);
    i++;
  }
  return out;
}

function blockFromLine(line: string): TiptapBlock | null {
  const trimmed = line.trim();
  if (trimmed.length === 0) return null;
  // Heading
  const h = /^(#{1,6})\s+(.*)$/.exec(trimmed);
  if (h) {
    const level = Math.min(h[1].length, 3) as 1 | 2 | 3;
    return { type: 'heading', attrs: { level }, content: tokenizeInline(h[2]) };
  }
  return { type: 'paragraph', content: tokenizeInline(trimmed) };
}

export function markdownToTiptap(md: string): TiptapDoc {
  const blocks: TiptapBlock[] = [];
  const lines = md.replace(/\r\n/g, '\n').split('\n');
  let i = 0;

  const flushParagraph = (buf: string[]) => {
    if (buf.length === 0) return;
    const joined = buf.join(' ').trim();
    if (joined.length === 0) return;
    blocks.push({ type: 'paragraph', content: tokenizeInline(joined) });
  };

  let paraBuf: string[] = [];

  while (i < lines.length) {
    const raw = lines[i];
    const line = raw.trimEnd();

    // Blank line — flush paragraph
    if (line.trim() === '') {
      flushParagraph(paraBuf);
      paraBuf = [];
      i++;
      continue;
    }

    // Fenced code block
    if (/^```/.test(line)) {
      flushParagraph(paraBuf);
      paraBuf = [];
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !/^```/.test(lines[i])) {
        codeLines.push(lines[i]);
        i++;
      }
      i++; // skip closing fence
      const codeText = codeLines.join('\n');
      blocks.push(
        codeText.length > 0
          ? { type: 'codeBlock', content: [{ type: 'text', text: codeText }] }
          : { type: 'codeBlock' },
      );
      continue;
    }

    // Heading
    const heading = /^(#{1,6})\s+(.*)$/.exec(line);
    if (heading) {
      flushParagraph(paraBuf);
      paraBuf = [];
      const level = Math.min(heading[1].length, 3) as 1 | 2 | 3;
      blocks.push({ type: 'heading', attrs: { level }, content: tokenizeInline(heading[2]) });
      i++;
      continue;
    }

    // Bullet list
    if (/^[-+*]\s+/.test(line.trim())) {
      flushParagraph(paraBuf);
      paraBuf = [];
      const items: TiptapBlock[] = [];
      while (i < lines.length && /^[-+*]\s+/.test(lines[i].trim())) {
        const item = lines[i].trim().replace(/^[-+*]\s+/, '');
        items.push({
          type: 'listItem',
          content: [{ type: 'paragraph', content: tokenizeInline(item) }],
        });
        i++;
      }
      blocks.push({ type: 'bulletList', content: items });
      continue;
    }

    // Ordered list
    if (/^\d+\.\s+/.test(line.trim())) {
      flushParagraph(paraBuf);
      paraBuf = [];
      const items: TiptapBlock[] = [];
      while (i < lines.length && /^\d+\.\s+/.test(lines[i].trim())) {
        const item = lines[i].trim().replace(/^\d+\.\s+/, '');
        items.push({
          type: 'listItem',
          content: [{ type: 'paragraph', content: tokenizeInline(item) }],
        });
        i++;
      }
      blocks.push({ type: 'orderedList', content: items });
      continue;
    }

    // Blockquote
    if (/^>\s?/.test(line)) {
      flushParagraph(paraBuf);
      paraBuf = [];
      const quoteLines: string[] = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) {
        quoteLines.push(lines[i].replace(/^>\s?/, ''));
        i++;
      }
      const inner = quoteLines.join('\n').trim();
      const child = blockFromLine(inner) ?? { type: 'paragraph' as const, content: tokenizeInline(inner) };
      blocks.push({ type: 'blockquote', content: [child] });
      continue;
    }

    // Regular paragraph line — accumulate
    paraBuf.push(line);
    i++;
  }
  flushParagraph(paraBuf);

  // Tiptap requires at least one block in a doc — fall back to an empty paragraph
  if (blocks.length === 0) blocks.push({ type: 'paragraph' });

  return { type: 'doc', content: blocks };
}
