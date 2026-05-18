"""Agent runner — Python port of apps/web/lib/agent.ts + streaming.ts."""
from __future__ import annotations

import json
import logging
import os
from typing import Any, AsyncGenerator

from claude_agent_sdk import query
from claude_agent_sdk.types import (
    AssistantMessage,
    ClaudeAgentOptions,
    McpSdkServerConfig,
    McpStdioServerConfig,
    ResultMessage,
    UserMessage,
)
from sqlalchemy.ext.asyncio import async_sessionmaker, AsyncSession

from api.agent.hooks import build_hooks
from api.agent.tools import build_chemclaw_mcp_server
from api.db.queries.session_store import scoped_session_store

logger = logging.getLogger(__name__)

BASE_SYSTEM_PROMPT = """You are ChemClaw, a pharma R&D knowledge-intelligence assistant.
You have access to an organization knowledge base, compound registry, and reaction database.
Always cite your sources. Never fabricate CAS numbers, yields, or experimental conditions.
When uncertain, say so explicitly rather than guessing.

For comprehensive, multi-section investigations, prefer dispatching to a
sub-agent via the Task tool with subagent_type='deep-research'. The sub-agent
runs in isolated context with retrieval tools only and returns a structured
markdown report — you then persist it via finalize_deep_research.

For citation-conflict resolution on a wiki page, dispatch
subagent_type='contradiction-resolver'. The sub-agent reads both citations
and the chunks that reference them, weighs the evidence, and returns a
proposed winner + reason that you persist via record_contradiction."""

DEEP_RESEARCH_PROMPT = """You are a focused research sub-agent for ChemClaw.

Your job: produce a structured markdown research report on the user's question.
You have retrieval tools only — no wiki writes, no campaign dispatches.

Plan first, then execute:
1. Use wiki_lookup to scope what the org already knows.
2. Drill in with wiki_lookup (slug or query), similarity searches, or ELN fetches as appropriate.
3. Pull at least 2 external sources via web_search → fetch_document.
4. Compose a 3-6 section markdown report with inline [N] citation markers.
5. Return the report body as your final assistant message.

Never fabricate CAS numbers, yields, or conditions."""

CONTRADICTION_RESOLVER_PROMPT = """You are a focused dispute-resolution sub-agent for ChemClaw.

Your job: weigh two citations on a wiki page and propose which is better supported.

Return exactly: WINNER: <citation_id>\nREASON: <one sentence>"""

MAX_PROMPT_BYTES = 100_000

# Per-block cap on text emitted in a single SSE `text` event. Without this,
# a single AssistantMessage text block containing a huge tool result can be
# json-encoded into one frame and held in memory while it's flushed. 1 MB is
# generous for normal chat text and small enough that tool-heavy sessions
# can't OOM the worker. Truncated frames carry a `[truncated]` marker.
SSE_TEXT_BLOCK_MAX_BYTES = 1_000_000


def _cap_text_block(text: str) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= SSE_TEXT_BLOCK_MAX_BYTES:
        return text
    return raw[:SSE_TEXT_BLOCK_MAX_BYTES].decode("utf-8", errors="ignore") + "\n[truncated]"


def _get_env(name: str, default: str = "") -> str:
    return os.environ.get(name, default)


async def run_agent_streaming(
    prompt: str,
    user_id: str,
    session_id: str | None,
    session_factory: async_sessionmaker[AsyncSession],
    plan_mode: bool = False,
) -> AsyncGenerator[str, None]:
    """Async generator yielding SSE-formatted data lines."""
    if len(prompt.encode()) > MAX_PROMPT_BYTES:
        yield f"data: {json.dumps({'type': 'error', 'message': 'prompt too large'})}\n\n"
        return

    # Substance gate is the single choke-point in api/routes/chat.py before
    # streaming begins. Don't re-fire it here: the prompt has already passed
    # the gate or carries a recorded override.

    project_key = f"chemclaw2:{user_id}"
    store = scoped_session_store(session_factory, project_key)
    mcp_server = build_chemclaw_mcp_server(user_id, session_id, session_factory)

    options = ClaudeAgentOptions(
        system_prompt=BASE_SYSTEM_PROMPT,
        session_store=store,
        resume=session_id,
        model=_get_env("ANTHROPIC_MODEL", "claude-opus-4-7-20251101"),
        max_turns=int(_get_env("AGENT_MAX_TURNS", "60")),
        permission_mode="plan" if plan_mode else None,
        mcp_servers={
            "chemclaw2-tools": McpSdkServerConfig(server=mcp_server),
            "mcp-molfp": McpStdioServerConfig(type="stdio", command="python", args=["-m", "mcp_molfp.server"]),
            "mcp-rxnfp": McpStdioServerConfig(type="stdio", command="python", args=["-m", "mcp_rxnfp.server"]),
        },
        hooks=build_hooks(user_id, project_key, session_factory),
        agents={
            "deep-research": {
                "description": (
                    "Multi-section research investigations. Use when the user asks for a comprehensive "
                    "review, a structured report, or any 'everything we know about X' question."
                ),
                "prompt": DEEP_RESEARCH_PROMPT,
                "mcpServers": ["chemclaw2-tools"],
                "maxTurns": 30,
            },
            "contradiction-resolver": {
                "description": "Weigh two conflicting citations on a wiki page.",
                "prompt": CONTRADICTION_RESOLVER_PROMPT,
                "mcpServers": ["chemclaw2-tools"],
                "maxTurns": 10,
            },
        },
    )

    # Emit the session_id immediately so the client can persist it before any
    # streaming tokens arrive. The session_id may be the one the client sent
    # (resume) or a freshly generated one from chat.py — either way it's the
    # authoritative id for this conversation turn.
    if session_id:
        yield f"data: {json.dumps({'type': 'session_start', 'session_id': session_id})}\n\n"

    result_session_id: str | None = None
    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, ResultMessage):
                result_session_id = message.session_id
                yield f"data: {json.dumps({'type': 'result', 'session_id': message.session_id, 'stop_reason': str(message.stop_reason)})}\n\n"
            elif isinstance(message, AssistantMessage):
                # Stream assistant text blocks
                for block in message.content:
                    if hasattr(block, 'text'):
                        text = _cap_text_block(block.text)
                        yield f"data: {json.dumps({'type': 'text', 'text': text})}\n\n"
                    elif getattr(block, 'type', None) == 'tool_use':
                        yield f"data: {json.dumps({'type': 'tool_use', 'name': getattr(block, 'name', '')})}\n\n"
            elif isinstance(message, UserMessage):
                # Tool results — surface to client so UI can show tool activity
                pass
    except Exception:
        logger.exception("agent_stream_error session=%s", session_id)
        yield f"data: {json.dumps({'type': 'error', 'message': 'An internal error occurred'})}\n\n"
    finally:
        yield "data: [DONE]\n\n"
