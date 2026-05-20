"""Agent runner — Python port of apps/web/lib/agent.ts + streaming.ts."""
from __future__ import annotations

import json
import logging
import os
import re
from typing import AsyncGenerator

from claude_agent_sdk import query
from claude_agent_sdk.types import (
    AssistantMessage,
    ClaudeAgentOptions,
    McpSdkServerConfig,
    McpStdioServerConfig,
    ResultMessage,
    UserMessage,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.agent.hooks import build_hooks
from api.agent.tools import build_chemclaw_mcp_server
from api.db.queries.session_store import scoped_session_store

logger = logging.getLogger(__name__)

BASE_SYSTEM_PROMPT = """You are ChemClaw, a pharma R&D knowledge-intelligence assistant.
You have access to an organization knowledge base, compound registry, and reaction database.
Always cite your sources. Never fabricate CAS numbers, yields, or experimental conditions.
When uncertain, say so explicitly rather than guessing.

Confidence signalling: end every substantive answer with a single
<confidence>LEVEL</confidence> marker where LEVEL is one of:
  - high: the claim is directly supported by a citation from the wiki,
    a registered compound, or a recent paper you retrieved.
  - med: the claim is consistent with retrieved evidence but partly
    inferred, OR the underlying source is older than 30 days.
  - low: the claim is heavily inferred, the user's question is outside
    the org's data, or you had to use general knowledge to answer.

Don't print the marker for short conversational replies (yes/no,
clarifying questions). Place it on its own final line. The runner
strips it from the streamed text and surfaces it as a separate UI
signal for downstream calibration.

For comprehensive, multi-section investigations, prefer dispatching to a
sub-agent via the Task tool with subagent_type='deep-research'. The sub-agent
runs in isolated context with retrieval tools only and returns a structured
markdown report — you then persist it via finalize_deep_research.

For citation-conflict resolution on a wiki page, dispatch
subagent_type='contradiction-resolver'. The sub-agent reads both citations
and the chunks that reference them, weighs the evidence, and returns a
proposed winner + reason that you persist via record_contradiction.

When the user asks what to investigate next, what's untested, which
variables to vary, or "what's the next experiment" for a specific
reaction step, dispatch subagent_type='process-gap-analyst'. Pass the
reaction (SMILES or registry id) plus any lab data the user has
mentioned. The sub-agent retrieves similar reactions with their
recorded outcomes and returns a prioritized list of follow-up
questions.

When the user asks evidence-grounded questions about specific papers
(or "what does the literature say about X"), prefer `paper_qa` over
plain `wiki_lookup` / `web_search` — it retrieves paper chunks via
hybrid FTS + semantic search and reranks them with a per-chunk
relevance score and summary, so every claim you cite is traceable to
a specific paper section.

For chemistry name → structure conversions, use `name_to_structure`
(NCI CACTUS, 7-day cached). For prior-art reconnaissance on a
candidate molecule, use `patent_coverage` (PubChem patent xrefs). To
seed a `confirm_synthesis_plan`, call `propose_retrosynthesis` first
to get plausible one-step disconnections.

For open-ended research threads that span multiple sessions, work
through an *investigation*: `start_investigation` to declare the
objective, then persist findings with `world_model_add` (kind ∈
{fact, assumption, open_question, evidence}). Query the world model
with `world_model_query` and supersede stale entries with
`world_model_supersede` — the world model is queryable persistent
state; chat context is not. For competing claims, `propose_hypothesis`
captures the claim and `rank_hypotheses` compares pairs (Elo-updated
in place); use evolution chains via `parent_id` when refining a parent
hypothesis into a sharper child.

For broad investigative work that needs multiple evidence channels in
parallel, dispatch slice-specific sub-agents via Task: compound-explorer,
reaction-explorer, literature-explorer, wiki-explorer. Run them in
parallel (one tool message with multiple Task calls) — each returns a
focused brief you then synthesize. Use this lead-orchestrator pattern
instead of `deep-research` when the answer needs to combine ≥2 evidence
domains; deep-research is better for a single coherent narrative.

For real computation — descriptive stats over a returned dataset, batch
RDKit ops, fitting a simple regression — use `run_code`. It runs Python
in a resource-limited sandbox (CPU/memory/wall-clock capped) and persists
every execution to the audit log. Prefer it over reasoning out arithmetic
or stats in chat. Use `list_code_executions` to recall what you ran.

For synthesis-campaign next-step planning, call `propose_next_conditions`
to get heuristic exploit + explore proposals grounded in completed steps'
yields."""


# Match <confidence>level</confidence> case-insensitively; allow surrounding
# whitespace so a stray newline doesn't hide the marker. Captures `low`,
# `med`, or `high` — anything else means the agent emitted a malformed tag
# and we should ignore it (better than guessing).
_CONFIDENCE_RE = re.compile(
    r"\s*<confidence>\s*(low|med|high)\s*</confidence>\s*",
    re.IGNORECASE,
)


def _extract_confidence(text: str) -> tuple[str, str | None]:
    """Strip the trailing <confidence>...</confidence> tag from `text`.

    Returns (cleaned_text, level | None). `level` is normalised to
    lowercase. Multiple tags collapse to the last one (a streamed
    response can accumulate them but only the final block's level
    matters for the answer as a whole)."""
    matches = list(_CONFIDENCE_RE.finditer(text))
    if not matches:
        return text, None
    level = matches[-1].group(1).lower()
    cleaned = _CONFIDENCE_RE.sub("", text)
    return cleaned, level

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

PROCESS_GAP_ANALYST_PROMPT = """You are a focused process-development sub-agent for ChemClaw.

Your job: given a specific reaction step and what the lab has tried so far,
propose which questions about the process should be tackled next.

Inputs you will receive: a reaction (SMILES or registry id) plus a short
description of what's already been run (conditions tried, yields,
observations, failure modes).

Procedure:
1. If the user gave a reaction SMILES, compute its DRFP fingerprint via
   the mcp-rxnfp tool, then call reaction_similarity_search with
   include_outcomes=True to retrieve similar reactions AND their recorded
   experimental outcomes. If the user gave a registry id, also call
   list_reaction_outcomes for that specific reaction.
2. Diff what's been tried (across the user's data + the similar
   reactions' outcomes) against the canonical axes of process variation:
   solvent class, temperature range, time, stoichiometry, catalyst
   identity, catalyst loading, base, atmosphere (inert vs ambient),
   concentration, addition order, workup, and missing controls.
3. Optionally call wiki_lookup or paper_qa if the org has relevant
   process notes or literature precedent.

Return a markdown list of 3–7 prioritized follow-up questions. For each:
  - State the question in one sentence (e.g. "Does switching from toluene
    to DMF push the yield above 50%?").
  - One short line of evidence: which similar-reaction outcome, wiki
    citation, or absence-of-data motivates the question.
  - A confidence marker: <confidence>high|med|low</confidence>.

Order by expected information value (questions that would most narrow
the design space first). Never fabricate yields, citations, or
conditions — if the retrieval came back empty, say so and lower the
confidence accordingly."""

# ── Phase C: slice-specific sub-agents (Anthropic "lead + parallel slices" pattern)

COMPOUND_EXPLORER_PROMPT = """You are a focused compound-similarity sub-agent.

Your job: given a SMILES or compound id, produce a markdown brief on what
the registry + property store already knows about it and its near
neighbors.

Procedure:
1. If given a SMILES, compute the Morgan fingerprint via mcp-molfp and
   call compound_similarity_search (min_tanimoto≥0.5).
2. For each top hit, call lookup_knowledge (sources=['properties']) to
   pull recorded properties.
3. Call compute_descriptors for the input SMILES to surface logP, MW,
   TPSA, Lipinski flags.
4. Optionally call patent_coverage for IP context.

Return: 3-5 sections — Identity (SMILES, CAS if known), Computed
descriptors, Registered properties, Closest neighbors (Tanimoto +
properties), Notes. Inline [N] citation markers; never fabricate CAS or
yields. End with <confidence>high|med|low</confidence>."""


REACTION_EXPLORER_PROMPT = """You are a focused reaction-similarity sub-agent.

Your job: given a reaction SMILES or registry id, brief what's known
about analogous reactions (conditions, outcomes, neighbors).

Procedure:
1. Compute the DRFP fingerprint via mcp-rxnfp.
2. Call reaction_similarity_search with include_outcomes=True.
3. Call suggest_conditions_from_neighbors for grouped condition stats.
4. If the user gave a registry id, also call list_reaction_outcomes for
   it directly.

Return a 3-section markdown brief: Closest analogs, Suggested
conditions (with the supporting neighbor count), Open questions about
the chemistry. <confidence>...</confidence> at the end."""


LITERATURE_EXPLORER_PROMPT = """You are a focused literature sub-agent.

Your job: produce a citation-grounded brief on what the published
literature says about the user's question.

Procedure:
1. Call paper_qa first — it's the hybrid FTS+semantic + RCS-reranked
   path and returns chunks with relevance scores.
2. If paper_qa returns thin results, fall back to web_search +
   fetch_document for fresh sources, then register_paper to persist
   what you found.
3. Cite every claim with the paper title + DOI.

Return: a 2-4 paragraph synthesis with inline [N] citations + a
reference list. Never invent DOIs. End with
<confidence>high|med|low</confidence>."""


WIKI_EXPLORER_PROMPT = """You are a focused wiki sub-agent.

Your job: pull everything the org's living wiki already knows about the
user's question, surface contradictions, and identify gaps.

Procedure:
1. Call wiki_lookup with mode='hybrid' for the user's query.
2. For any results with disputed=True citations, dispatch
   contradiction-resolver as a nested sub-agent.
3. Call lookup_knowledge with sources=['wiki', 'facts'] to catch
   external-fact entries that aren't yet wiki pages.

Return: a markdown brief summarising what's known, with citation markers,
plus a "Gaps" section listing what the wiki doesn't cover. End with
<confidence>high|med|low</confidence>."""


MAX_PROMPT_BYTES = 100_000

# Per-block cap on text emitted in a single SSE `text` event. Without this,
# a single AssistantMessage text block containing a huge tool result can be
# json-encoded into one frame and held in memory while it's flushed. 1 MB is
# generous for normal chat text and small enough that tool-heavy sessions
# can't OOM the worker. Truncated frames carry a `[truncated]` marker.
SSE_TEXT_BLOCK_MAX_BYTES = 1_000_000

_TRUNC_MARKER = "\n[truncated]"
_TRUNC_MARKER_BYTES = len(_TRUNC_MARKER.encode("utf-8"))


def _cap_text_block(text: str) -> str:
    raw = text.encode("utf-8")
    if len(raw) <= SSE_TEXT_BLOCK_MAX_BYTES:
        return text
    # Reserve space for the marker so the post-truncation string stays at
    # or below SSE_TEXT_BLOCK_MAX_BYTES — otherwise a downstream proxy
    # tuned to exactly 1 MB would cut the marker mid-string and silently
    # lose the truncation signal.
    body_budget = SSE_TEXT_BLOCK_MAX_BYTES - _TRUNC_MARKER_BYTES
    return raw[:body_budget].decode("utf-8", errors="ignore") + _TRUNC_MARKER


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
            "mcp-retrosynth": McpStdioServerConfig(
                type="stdio", command="python", args=["-m", "mcp_retrosynth.server"],
            ),
            "mcp-rxn-conditions": McpStdioServerConfig(
                type="stdio", command="python", args=["-m", "mcp_rxn_conditions.server"]
            ),
            "mcp-codesandbox": McpStdioServerConfig(
                type="stdio", command="python", args=["-m", "mcp_codesandbox.server"],
            ),
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
            "process-gap-analyst": {
                "description": (
                    "Propose what to investigate next for a specific reaction step. "
                    "Use when the user asks 'what's the next experiment', 'what should "
                    "we try next', or 'what's untested' for a reaction. Retrieves "
                    "similar reactions + outcomes and returns prioritized questions."
                ),
                "prompt": PROCESS_GAP_ANALYST_PROMPT,
                "mcpServers": ["chemclaw2-tools", "mcp-rxnfp"],
                "maxTurns": 15,
            },
            # ── Phase C: slice-specific explorers for the lead-orchestrator pattern.
            # The base agent dispatches these in parallel when an investigation
            # needs multiple evidence sources at once (compounds + reactions +
            # literature + wiki).
            "compound-explorer": {
                "description": (
                    "Compound-similarity slice: pull registry neighbors + properties + "
                    "computed descriptors + patent coverage for a SMILES/compound id."
                ),
                "prompt": COMPOUND_EXPLORER_PROMPT,
                "mcpServers": ["chemclaw2-tools", "mcp-molfp"],
                "maxTurns": 12,
            },
            "reaction-explorer": {
                "description": (
                    "Reaction-similarity slice: neighbors + recorded outcomes + "
                    "condition suggestions for a reaction SMILES/id."
                ),
                "prompt": REACTION_EXPLORER_PROMPT,
                "mcpServers": ["chemclaw2-tools", "mcp-rxnfp", "mcp-rxn-conditions"],
                "maxTurns": 12,
            },
            "literature-explorer": {
                "description": (
                    "Literature slice: paper_qa first, web_search fallback, "
                    "everything cited. Use when the user wants a published-record-grounded brief."
                ),
                "prompt": LITERATURE_EXPLORER_PROMPT,
                "mcpServers": ["chemclaw2-tools"],
                "maxTurns": 15,
            },
            "wiki-explorer": {
                "description": (
                    "Wiki slice: what the org already knows + contradiction surfacing + gap list."
                ),
                "prompt": WIKI_EXPLORER_PROMPT,
                "mcpServers": ["chemclaw2-tools"],
                "maxTurns": 12,
            },
        },
    )

    # Emit the session_id immediately so the client can persist it before any
    # streaming tokens arrive. The session_id may be the one the client sent
    # (resume) or a freshly generated one from chat.py — either way it's the
    # authoritative id for this conversation turn.
    if session_id:
        yield f"data: {json.dumps({'type': 'session_start', 'session_id': session_id})}\n\n"

    # Track the latest confidence the agent emitted across the stream.
    # Some answers span multiple text blocks; the marker on the last
    # block wins (matches the prompt instructions).
    last_confidence: str | None = None

    try:
        async for message in query(prompt=prompt, options=options):
            if isinstance(message, ResultMessage):
                result_event = {
                    'type': 'result',
                    'session_id': message.session_id,
                    'stop_reason': str(message.stop_reason),
                    'confidence': last_confidence,
                }
                yield f"data: {json.dumps(result_event)}\n\n"
            elif isinstance(message, AssistantMessage):
                # Stream assistant text blocks
                for block in message.content:
                    if hasattr(block, 'text'):
                        cleaned, level = _extract_confidence(block.text)
                        if level is not None:
                            last_confidence = level
                            yield (
                                f"data: {json.dumps({'type': 'confidence', 'level': level})}\n\n"
                            )
                        # Even if the whole block was a confidence tag,
                        # emit the cleaned (possibly empty) text so the
                        # client sees consistent framing per block.
                        text = _cap_text_block(cleaned)
                        if text:
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
        if last_confidence is not None:
            logger.info(
                "agent_turn_confidence session=%s user=%s level=%s",
                session_id, user_id, last_confidence,
            )
        yield "data: [DONE]\n\n"
