import { TABLE_DIVIDER_RE } from './wiki-tables';

// Defends `chunkText` against pathological inputs that would amplify the
// table-divider regex into catastrophic backtracking, or that would consume
// gigabytes of memory during paragraph/sentence splitting.
const MAX_CHUNK_INPUT_CHARS = 1_000_000;
const FENCE_RE = /^\s*```/;

/**
 * Split a markdown body into table blocks and non-table fragments. Each
 * fragment is either `{ kind: 'table', text }` (preserved verbatim) or
 * `{ kind: 'prose', text }` (handed to the paragraph/sentence splitter).
 * The chunker emits tables as single chunks and shards prose fragments
 * normally.
 *
 * Tracks fenced code blocks so pipe-tables in code examples don't get
 * parsed as real tables, and stops table parsing when the inner loop
 * encounters another divider row (adjacent tables split cleanly).
 */
function splitOnTables(md: string): Array<{ kind: 'table' | 'prose'; text: string }> {
  const lines = md.replace(/\r\n/g, '\n').split('\n');
  const out: Array<{ kind: 'table' | 'prose'; text: string }> = [];
  let proseBuf: string[] = [];
  let inFence = false;
  const flushProse = () => {
    if (proseBuf.length === 0) return;
    const joined = proseBuf.join('\n');
    if (joined.trim().length > 0) out.push({ kind: 'prose', text: joined });
    proseBuf = [];
  };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (FENCE_RE.test(line)) {
      inFence = !inFence;
      proseBuf.push(line);
      continue;
    }
    if (inFence) {
      proseBuf.push(line);
      continue;
    }
    const next = lines[i + 1];
    if (line.includes('|') && next && TABLE_DIVIDER_RE.test(next.trim())) {
      flushProse();
      const tableLines: string[] = [line, next];
      let j = i + 2;
      while (j < lines.length) {
        const rowLine = lines[j];
        if (!rowLine.includes('|') || rowLine.trim().length === 0) break;
        if (TABLE_DIVIDER_RE.test(rowLine.trim())) break;
        const peek = lines[j + 1];
        if (peek && TABLE_DIVIDER_RE.test(peek.trim())) break;
        tableLines.push(rowLine);
        j++;
      }
      out.push({ kind: 'table', text: tableLines.join('\n') });
      i = j - 1;
      continue;
    }
    proseBuf.push(line);
  }
  flushProse();
  return out;
}

/**
 * Split text into semantically coherent chunks for embedding.
 *
 * Strategy (in order of preference):
 *   1. Markdown tables — emit each table as a single chunk so row↔column
 *      semantics survive embedding. Without this, header and data rows land
 *      in different chunks and a query for "yield" loses the corresponding
 *      catalyst/temperature context.
 *   2. Paragraph boundaries (\n\n).
 *   3. Sentence boundaries.
 *   4. Word boundaries — final fallback for run-on sentences > maxSize.
 *
 * Adjacent chunks share an `overlap`-char prefix from the previous chunk so
 * queries straddling a paragraph boundary still hit at least one chunk.
 */
export function chunkText(text: string, maxSize = 1200, overlap = 200): string[] {
  if (text.length > MAX_CHUNK_INPUT_CHARS) {
    throw new Error(`chunkText: input exceeds ${MAX_CHUNK_INPUT_CHARS} chars (got ${text.length})`);
  }
  const segments = splitOnTables(text);
  const out: string[] = [];
  for (const seg of segments) {
    if (seg.kind === 'table') {
      const t = seg.text.trim();
      if (t.length > 10) out.push(t);
      continue;
    }
    out.push(...chunkProse(seg.text, maxSize, overlap));
  }
  return out;
}

function chunkProse(text: string, maxSize: number, overlap: number): string[] {
  const paragraphs = text.split(/\n{2,}/).map((p) => p.trim()).filter((p) => p.length > 0);
  const result: string[] = [];

  const pushWithOverlap = (chunk: string, prependPrevTail: boolean) => {
    const t = chunk.trim();
    if (t.length <= 10) return;
    if (prependPrevTail && result.length > 0) {
      const prev = result[result.length - 1];
      const tail = prev.length > overlap ? prev.slice(-overlap) : prev;
      result.push(`${tail} ${t}`);
    } else {
      result.push(t);
    }
  };

  for (const para of paragraphs) {
    if (para.length <= maxSize) {
      pushWithOverlap(para, true);
      continue;
    }
    const sentences = para.split(/(?<=[.!?])\s+/).filter((s) => s.length > 0);
    let current = '';
    let firstInPara = true;
    for (const sentence of sentences) {
      if ((current + ' ' + sentence).trim().length <= maxSize) {
        current = current ? current + ' ' + sentence : sentence;
      } else {
        if (current.length > 10) {
          pushWithOverlap(current.trim(), firstInPara);
          firstInPara = false;
        }
        const overlapText = current.length > overlap ? current.slice(-overlap) : current;
        current = overlapText + ' ' + sentence;
      }
    }
    const flushed = current.trim();
    if (flushed.length > maxSize) {
      const words = flushed.split(/\s+/);
      let sub = '';
      for (const word of words) {
        if ((sub + ' ' + word).trim().length <= maxSize) {
          sub = sub ? sub + ' ' + word : word;
        } else {
          if (sub.length > 10) {
            pushWithOverlap(sub.trim(), firstInPara);
            firstInPara = false;
          }
          sub = word;
        }
      }
      if (sub.trim().length > 10) {
        pushWithOverlap(sub.trim(), firstInPara);
      }
    } else if (flushed.length > 10) {
      pushWithOverlap(flushed, firstInPara);
    }
  }

  return result;
}
