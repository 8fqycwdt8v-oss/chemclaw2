'use client';
/* eslint-disable react-hooks/exhaustive-deps */
import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';

type ToolUse = { id: string; name: string; input: unknown };
type Message =
  | { role: 'user'; text: string }
  | { role: 'assistant'; text: string; toolUses: ToolUse[]; citations: Citation[] }
  | { role: 'error'; text: string };

type Citation = { slug: string; label: string };

const SLUG_RE = /\b([a-z0-9][a-z0-9-]{1,80})\b/g;

function extractWikiSlugs(text: string, knownSlugs: Set<string>): Citation[] {
  const found: Citation[] = [];
  const seen = new Set<string>();
  for (const m of text.matchAll(SLUG_RE)) {
    const slug = m[1];
    if (knownSlugs.has(slug) && !seen.has(slug)) {
      seen.add(slug);
      found.push({ slug, label: slug });
    }
  }
  return found;
}

export function ChatClient() {
  const router = useRouter();
  const sp = useSearchParams();
  const sessionIdRef = useRef<string | null>(sp.get('session'));
  const [messages, setMessages] = useState<Message[]>([]);
  const [input, setInput] = useState('');
  const [streaming, setStreaming] = useState(false);
  const [knownSlugs, setKnownSlugs] = useState<Set<string>>(new Set());
  const abortRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);

  // Pull all wiki slugs once so we can render inline citations in assistant text.
  useEffect(() => {
    fetch('/api/wiki')
      .then((r) => (r.ok ? r.json() : null))
      .then((data: { pages?: Array<{ slug: string }> } | null) => {
        if (data?.pages) setKnownSlugs(new Set(data.pages.map((p) => p.slug)));
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const send = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      if (!trimmed || streaming) return;
      setInput('');
      setMessages((m) => [
        ...m,
        { role: 'user', text: trimmed },
        { role: 'assistant', text: '', toolUses: [], citations: [] },
      ]);
      setStreaming(true);
      const controller = new AbortController();
      abortRef.current = controller;

      try {
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            prompt: trimmed,
            sessionId: sessionIdRef.current ?? undefined,
          }),
          signal: controller.signal,
        });
        if (!res.ok || !res.body) {
          const err = await res.text().catch(() => 'request failed');
          setMessages((m) => [...m, { role: 'error', text: err }]);
          return;
        }
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        let buf = '';
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += decoder.decode(value, { stream: true });
          const lines = buf.split('\n');
          buf = lines.pop() ?? '';
          for (const line of lines) {
            if (!line.startsWith('data: ')) continue;
            const payload = line.slice(6);
            if (payload === '[DONE]') continue;
            try {
              const evt = JSON.parse(payload) as Record<string, unknown>;
              applyEvent(evt);
            } catch {
              // ignore malformed SSE frame
            }
          }
        }
      } catch (err) {
        if ((err as Error).name !== 'AbortError') {
          setMessages((m) => [...m, { role: 'error', text: (err as Error).message }]);
        }
      } finally {
        setStreaming(false);
        abortRef.current = null;
      }
    },
    [streaming],
  );

  function applyEvent(evt: Record<string, unknown>) {
    const type = evt.type as string | undefined;
    if (type === 'system' && typeof evt.session_id === 'string') {
      sessionIdRef.current = evt.session_id;
      router.replace(`/chat?session=${evt.session_id}`);
      return;
    }
    if (type === 'error') {
      setMessages((m) => [...m, { role: 'error', text: String(evt.message ?? 'error') }]);
      return;
    }
    const message = evt.message as { role?: string; content?: unknown[] } | undefined;
    if (!message || !Array.isArray(message.content)) return;
    for (const block of message.content as Array<Record<string, unknown>>) {
      if (block.type === 'text' && typeof block.text === 'string') {
        appendAssistantText(block.text);
      } else if (block.type === 'tool_use') {
        appendToolUse({
          id: String(block.id ?? ''),
          name: String(block.name ?? ''),
          input: block.input,
        });
      }
    }
  }

  function appendAssistantText(chunk: string) {
    setMessages((m) => {
      const last = m[m.length - 1];
      if (!last || last.role !== 'assistant') return m;
      const text = last.text + chunk;
      return [
        ...m.slice(0, -1),
        { ...last, text, citations: extractWikiSlugs(text, knownSlugs) },
      ];
    });
  }

  function appendToolUse(use: ToolUse) {
    setMessages((m) => {
      const last = m[m.length - 1];
      if (!last || last.role !== 'assistant') return m;
      return [...m.slice(0, -1), { ...last, toolUses: [...last.toolUses, use] }];
    });
  }

  const cancel = () => {
    abortRef.current?.abort();
  };

  const presetAnalytical = () => {
    const technique = prompt('Technique (NMR / MS / IR):');
    if (!technique) return;
    const observations = prompt('Observations:');
    if (!observations) return;
    const structure = prompt('Proposed structure SMILES (optional):') ?? '';
    const text =
      `Use interpret_analytical_result to interpret these data:\n` +
      `technique: ${technique}\nobservations: ${observations}` +
      (structure ? `\nproposed_structure_smiles: ${structure}` : '');
    void send(text);
  };

  return (
    <div className="flex flex-col h-[calc(100vh-7rem)]">
      <div className="flex-1 overflow-y-auto space-y-4 pb-4">
        {messages.length === 0 && (
          <div className="text-slate-500 text-sm">
            Ask a chemistry question — the agent has access to compound, reaction, and wiki search.
          </div>
        )}
        {messages.map((m, i) => (
          <MessageView key={i} msg={m} />
        ))}
        <div ref={endRef} />
      </div>
      <form
        className="border-t pt-3 flex gap-2 items-end"
        onSubmit={(e) => {
          e.preventDefault();
          void send(input);
        }}
      >
        <button
          type="button"
          onClick={presetAnalytical}
          className="text-xs px-2 py-1 border rounded text-slate-700 hover:bg-slate-50"
          disabled={streaming}
        >
          Interpret analytical data
        </button>
        <textarea
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              void send(input);
            }
          }}
          rows={2}
          placeholder="Ask about a compound, reaction, or transformation…"
          className="flex-1 border rounded p-2 text-sm resize-none"
          disabled={streaming}
        />
        {streaming ? (
          <button type="button" onClick={cancel} className="px-4 py-2 border rounded text-sm">
            Cancel
          </button>
        ) : (
          <button type="submit" className="px-4 py-2 bg-blue-600 text-white rounded text-sm">
            Send
          </button>
        )}
      </form>
    </div>
  );
}

function MessageView({ msg }: { msg: Message }) {
  if (msg.role === 'user') {
    return (
      <div className="bg-slate-100 rounded p-3 text-sm whitespace-pre-wrap">{msg.text}</div>
    );
  }
  if (msg.role === 'error') {
    return <div className="text-red-700 text-sm bg-red-50 p-2 rounded">Error: {msg.text}</div>;
  }
  return (
    <div className="space-y-2">
      {msg.toolUses.map((t) => (
        <div key={t.id} className="text-xs text-slate-500 border-l-2 border-slate-300 pl-2">
          tool: <span className="font-mono">{t.name}</span>
        </div>
      ))}
      <div className="text-sm whitespace-pre-wrap">{msg.text || (<span className="text-slate-400">…</span>)}</div>
      {msg.citations.length > 0 && (
        <div className="flex flex-wrap gap-1 pt-1">
          {msg.citations.map((c) => (
            <Link
              key={c.slug}
              href={`/wiki/${c.slug}`}
              className="text-xs bg-blue-50 text-blue-700 px-2 py-0.5 rounded border border-blue-200"
            >
              {c.label}
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
