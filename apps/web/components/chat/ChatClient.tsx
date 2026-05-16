'use client';
/* eslint-disable react-hooks/exhaustive-deps */
import { useCallback, useEffect, useRef, useState } from 'react';
import Link from 'next/link';
import { useRouter, useSearchParams } from 'next/navigation';

type ToolUse = { id: string; name: string; input: unknown; output?: string };
type Message =
  | { role: 'user'; text: string }
  | { role: 'assistant'; text: string; toolUses: ToolUse[]; citations: Citation[] }
  | { role: 'error'; text: string };

type Citation = { slug: string; label: string };
type Todo = { id: string; text: string; status: 'pending' | 'done'; position: number };

// Citation slugs must contain a hyphen (e.g. "aspirin-synthesis") to avoid
// false-positive links on plain words. Single-word wiki slugs are skipped
// from inline citation rendering; they can still be reached via /wiki list.
const SLUG_RE = /\b([a-z][a-z0-9]+(?:-[a-z0-9]+)+)\b/g;

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
  const [todos, setTodos] = useState<Todo[]>([]);
  const [todosOpen, setTodosOpen] = useState(true);
  const abortRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);

  // v2.1-B2: refresh the agent todo list. Called after every assistant turn
  // completes (begin_deep_research seeds them; finalize_deep_research marks
  // them done) and once on session-resume.
  const refreshTodos = useCallback(async () => {
    const sid = sessionIdRef.current;
    if (!sid) return;
    try {
      const res = await fetch(`/api/session/${sid}/todos`);
      if (!res.ok) return;
      const data = (await res.json()) as { todos?: Todo[] };
      setTodos(data.todos ?? []);
    } catch {
      // Pure UX nicety — silent on network blip.
    }
  }, []);

  const toggleTodo = useCallback(async (todo: Todo) => {
    const sid = sessionIdRef.current;
    if (!sid) return;
    const next = todo.status === 'done' ? 'pending' : 'done';
    setTodos((ts) => ts.map((t) => (t.id === todo.id ? { ...t, status: next } : t)));
    try {
      await fetch(`/api/session/${sid}/todos`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id: todo.id, status: next }),
      });
    } catch {
      // Revert the optimistic update on failure.
      setTodos((ts) => ts.map((t) => (t.id === todo.id ? { ...t, status: todo.status } : t)));
    }
  }, []);

  // Pull all wiki slugs once so we can render inline citations in assistant text.
  useEffect(() => {
    fetch('/api/wiki')
      .then((r) => (r.ok ? r.json() : null))
      .then((data: { pages?: Array<{ slug: string }> } | null) => {
        if (data?.pages) setKnownSlugs(new Set(data.pages.map((p) => p.slug)));
      })
      .catch(() => {});
  }, []);

  // Abort the in-flight SSE stream if the user navigates away mid-response.
  useEffect(() => () => abortRef.current?.abort(), []);

  // Poll /api/notifications every 30s when the tab is visible. Surfaces
  // completed-campaign events as toasts and refreshes the nav badge.
  // GET is idempotent; we then POST campaign ids to mark them acknowledged.
  useEffect(() => {
    let cancelled = false;
    const poll = async () => {
      if (document.visibilityState !== 'visible') return;
      try {
        const res = await fetch('/api/notifications');
        if (!res.ok || cancelled) return;
        const data = (await res.json()) as {
          campaigns: Array<{ id: string; targetSmiles: string | null; status: string; wikiPageId: string | null }>;
        };
        if (data.campaigns.length === 0) return;
        for (const c of data.campaigns) {
          setMessages((m) => [
            ...m,
            {
              role: 'error',
              text: `Campaign ${c.targetSmiles ?? c.id.slice(0, 8)} ${c.status}${c.wikiPageId ? ' — wiki page ready' : ''}`,
            },
          ]);
        }
        // Acknowledge so the next poll doesn't re-surface the same events.
        await fetch('/api/notifications', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ campaignIds: data.campaigns.map((c) => c.id) }),
        }).catch(() => {});
      } catch {
        // network glitches are fine; the next tick will retry
      }
    };
    const handle = setInterval(poll, 30_000);
    return () => { cancelled = true; clearInterval(handle); };
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  const send = useCallback(
    async (text: string, overrideJustification?: string, planMode?: boolean) => {
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
            ...(overrideJustification ? { override_justification: overrideJustification } : {}),
            ...(planMode ? { plan_mode: true } : {}),
          }),
          signal: controller.signal,
        });
        if (res.status === 403) {
          const body = (await res.json().catch(() => null)) as
            | { error?: string; override_available?: boolean; override_hint?: string }
            | null;
          if (body?.override_available && !overrideJustification) {
            const j = prompt(`${body.error}\n\n${body.override_hint}\n\nProvide justification:`);
            if (j && j.trim().length >= 20) {
              setMessages((m) => m.slice(0, -2));
              setStreaming(false);
              abortRef.current = null;
              await send(text, j.trim());
              return;
            }
          }
          setMessages((m) => [...m, { role: 'error', text: body?.error ?? 'Forbidden' }]);
          return;
        }
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
        setMessages((m) => {
          const last = m[m.length - 1];
          if (!last || last.role !== 'assistant') return m;
          return [
            ...m.slice(0, -1),
            { ...last, citations: extractWikiSlugs(last.text, knownSlugs) },
          ];
        });
        void refreshTodos();
      }
    },
    [streaming, refreshTodos, knownSlugs],
  );

  // Pull todos once on mount for the session-resume case (the user reopens an
  // older session that already has a checklist on file).
  useEffect(() => { void refreshTodos(); }, [refreshTodos]);

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
      } else if (block.type === 'tool_result') {
        const useId = String(block.tool_use_id ?? '');
        const text = Array.isArray(block.content)
          ? (block.content as Array<{ text?: string }>).map((c) => c.text ?? '').join('')
          : typeof block.content === 'string' ? block.content : JSON.stringify(block.content ?? '');
        appendToolResult(useId, text);
      }
    }
  }

  function appendToolResult(toolUseId: string, output: string) {
    setMessages((m) => {
      // Tool results often come on the next assistant turn — find the most recent
      // assistant message and attach to the matching tool_use.
      for (let i = m.length - 1; i >= 0; i--) {
        const msg = m[i];
        if (msg.role !== 'assistant') continue;
        const idx = msg.toolUses.findIndex((u) => u.id === toolUseId);
        if (idx === -1) continue;
        const updated = msg.toolUses.map((u, j) => (j === idx ? { ...u, output } : u));
        return [...m.slice(0, i), { ...msg, toolUses: updated }, ...m.slice(i + 1)];
      }
      return m;
    });
  }

  function appendAssistantText(chunk: string) {
    // Wave-1 A7: do NOT re-scan the full text for wiki slugs on every SSE
    // chunk. The same regex was running ~50 × per assistant turn. Citations
    // are populated once when the stream finishes (see send()'s finally
    // block via finalizeCitations), so per-chunk we only need to append.
    setMessages((m) => {
      const last = m[m.length - 1];
      if (!last || last.role !== 'assistant') return m;
      return [
        ...m.slice(0, -1),
        { ...last, text: last.text + chunk },
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

  const presetDeepResearch = () => {
    const question = window.prompt('Deep-research question (multi-section investigation):');
    if (!question) return;
    void send(
      `Call begin_deep_research with this question, follow the returned checklist ` +
      `to gather evidence over the next several turns, then call finalize_deep_research ` +
      `to persist the report:\n\n${question}`,
    );
  };

  const presetPlan = () => {
    // Wave-1 A1: native SDK plan mode. The previous prompt-engineered
    // `[PLAN MODE]` prefix instructed the model to plan; the SDK now enforces
    // it (permissionMode='plan', no tool execution). The agent presents the
    // plan; the user re-sends the same question without plan mode to run it.
    const question = window.prompt('What question or task should be planned step-by-step?');
    if (!question) return;
    void send(question, undefined, true);
  };

  const presetSaveSkill = async () => {
    const sessionId = sessionIdRef.current;
    if (!sessionId) return;
    const name = window.prompt('Skill name (lowercase kebab-case, 2-40 chars):');
    if (!name) return;
    const description = window.prompt('One-line description:');
    if (!description) return;
    try {
      const res = await fetch('/api/skills', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sessionId, name, description }),
      });
      const body = (await res.json().catch(() => null)) as { error?: string; path?: string; note?: string } | null;
      if (!res.ok) throw new Error(body?.error ?? `Save skill failed (${res.status})`);
      setMessages((m) => [...m, { role: 'error', text: `Skill saved: ${body?.path}. ${body?.note ?? ''}` }]);
    } catch (err) {
      setMessages((m) => [...m, { role: 'error', text: (err as Error).message }]);
    }
  };

  const presetFeedback = async (score: 1 | -1) => {
    const sessionId = sessionIdRef.current;
    if (!sessionId) {
      setMessages((m) => [...m, { role: 'error', text: 'No active session to grade.' }]);
      return;
    }
    // turnIndex = number of completed assistant messages (the most-recent one is the one being graded)
    const turnIndex = messages.filter((mm) => mm.role === 'assistant').length - 1;
    if (turnIndex < 0) return;
    const reason = window.prompt(`Reason (optional) for ${score === 1 ? '👍' : '👎'}:`) ?? null;
    try {
      const res = await fetch('/api/feedback', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sessionId, turnIndex, score, reason }),
      });
      if (!res.ok) throw new Error(`Feedback failed (${res.status})`);
    } catch (err) {
      setMessages((m) => [...m, { role: 'error', text: (err as Error).message }]);
    }
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

  const presetApproveStep = async () => {
    const campaignId = prompt('Campaign ID:');
    if (!campaignId) return;
    const stepIdx = prompt('Step index to approve:');
    if (!stepIdx) return;
    try {
      const res = await fetch(`/api/campaigns/${campaignId}/steps/${stepIdx}/approve`, { method: 'POST' });
      const body = (await res.json().catch(() => null)) as { error?: string; approved?: boolean } | null;
      if (!res.ok) throw new Error(body?.error ?? `Approve failed (${res.status})`);
      setMessages((m) => [...m, { role: 'error', text: `Step ${stepIdx} approved — worker will execute on next sweep.` }]);
    } catch (err) {
      setMessages((m) => [...m, { role: 'error', text: (err as Error).message }]);
    }
  };

  return (
    <div className="flex flex-col h-[calc(100vh-7rem)]">
      {todos.length > 0 && (
        <div className="border rounded mb-2 text-xs">
          <button
            type="button"
            onClick={() => setTodosOpen((v) => !v)}
            className="w-full text-left px-2 py-1 bg-slate-50 text-slate-700 flex justify-between"
            aria-expanded={todosOpen}
          >
            <span>
              Research checklist · {todos.filter((t) => t.status === 'done').length}/{todos.length} done
            </span>
            <span>{todosOpen ? '▾' : '▸'}</span>
          </button>
          {todosOpen && (
            <ul className="px-2 py-1 space-y-1">
              {todos.map((t) => (
                <li key={t.id} className="flex items-start gap-2">
                  <input
                    type="checkbox"
                    checked={t.status === 'done'}
                    onChange={() => void toggleTodo(t)}
                    className="mt-0.5"
                    aria-label={`Mark "${t.text}" as ${t.status === 'done' ? 'pending' : 'done'}`}
                  />
                  <span className={t.status === 'done' ? 'line-through text-slate-400' : ''}>
                    {t.text}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
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
        <button
          type="button"
          onClick={presetApproveStep}
          className="text-xs px-2 py-1 border rounded text-slate-700 hover:bg-slate-50"
          disabled={streaming}
        >
          Approve next step
        </button>
        <button
          type="button"
          onClick={presetPlan}
          className="text-xs px-2 py-1 border rounded text-slate-700 hover:bg-slate-50"
          disabled={streaming}
          title="Have the agent draft a step-by-step plan you can approve before it runs"
        >
          Plan first
        </button>
        <button
          type="button"
          onClick={presetDeepResearch}
          className="text-xs px-2 py-1 border rounded text-slate-700 hover:bg-slate-50"
          disabled={streaming}
          title="Have the agent compose a structured research report and save it to the wiki"
        >
          Deep research
        </button>
        <button
          type="button"
          onClick={() => void presetFeedback(1)}
          className="text-xs px-2 py-1 border rounded text-slate-700 hover:bg-slate-50"
          disabled={streaming || messages.filter((mm) => mm.role === 'assistant').length === 0}
          title="Grade the most recent assistant turn 👍"
        >
          👍
        </button>
        <button
          type="button"
          onClick={() => void presetFeedback(-1)}
          className="text-xs px-2 py-1 border rounded text-slate-700 hover:bg-slate-50"
          disabled={streaming || messages.filter((mm) => mm.role === 'assistant').length === 0}
          title="Grade the most recent assistant turn 👎"
        >
          👎
        </button>
        <button
          type="button"
          onClick={() => void presetSaveSkill()}
          className="text-xs px-2 py-1 border rounded text-slate-700 hover:bg-slate-50"
          disabled={streaming || messages.filter((mm) => mm.role === 'assistant').length === 0}
          title="Save the last turn as a reusable skill"
        >
          Save as skill
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
      {msg.toolUses.map((t) => <ToolCard key={t.id} use={t} />)}
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

function ToolCard({ use }: { use: ToolUse }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="text-xs border-l-2 border-slate-300 pl-2">
      <button onClick={() => setOpen((v) => !v)} className="text-slate-600">
        tool: <span className="font-mono">{use.name}</span> {open ? '▾' : '▸'}
      </button>
      {open && (
        <div className="mt-1 space-y-1">
          <div>
            <span className="text-slate-500">input:</span>
            <pre className="font-mono text-xs bg-slate-50 p-1 rounded mt-0.5 overflow-x-auto">
              {JSON.stringify(use.input, null, 2)}
            </pre>
          </div>
          {use.output !== undefined && (
            <div>
              <span className="text-slate-500">output:</span>
              <pre className="font-mono text-xs bg-slate-50 p-1 rounded mt-0.5 overflow-x-auto max-h-48">
                {use.output.length > 2000 ? use.output.slice(0, 2000) + '\n…(truncated)' : use.output}
              </pre>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
