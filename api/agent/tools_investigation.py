"""Investigations + hypotheses + code-execution MCP tools.

13 tools forming the research-thread cluster (Kosmos-style world model +
Google AI Co-Scientist tournament + sandbox runs anchored to a thread).

`build_investigation_tools(user_id, session_id, session_factory)`
returns the `SdkMcpTool` list. All 13 need `user_id` (owner scoping);
`run_code` and `list_code_executions` also need `session_id` so a
chat-anchored sandbox run can fall back to that scope when no
investigation_id is given.
"""
from __future__ import annotations

import base64
import binascii
import hashlib
import json
import logging
from typing import Any

from claude_agent_sdk import SdkMcpTool
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from api.agent.tool_adapter import wrap_tool

logger = logging.getLogger(__name__)


class FigureIssue(BaseModel):
    """One problem the vision critic flagged on a figure."""

    kind: str
    detail: str


class FigureCritique(BaseModel):
    """Structured result of a single vision-model figure critique."""

    ok: bool
    severity: str  # 'none' | 'minor' | 'major'
    issues: list[FigureIssue] = Field(default_factory=list)


_FIGURE_CRITIQUE_PROMPT = """You are a meticulous data-visualization reviewer for a \
scientific knowledge base. Examine the attached figure as if it were about to be \
cited as evidence in a wiki page. Flag only real, visible problems that would \
mislead a reader or make the figure unusable as evidence.

Check for: missing or unreadable axis labels, missing units, absent legend when \
multiple series are shown, truncated or non-zero-baseline axes that exaggerate \
effects, illegible text, overlapping/clipped elements, and obvious mislabeling.

Reply with EXACTLY one JSON object inside a ```json fenced block. No prose.

```json
{"ok": <true if the figure is sound enough to cite, false otherwise>,
 "severity": "<none|minor|major>",
 "issues": [{"kind": "<one of: missing_legend, unlabeled_axes, missing_units, \
misleading_scale, truncated_axis, illegible, mislabeled, other>",
             "detail": "<one concise sentence>"}]}
```

Use severity 'none' with an empty issues list when the figure is clean. Use \
'minor' for cosmetic issues that don't undermine the evidence, and 'major' \
(with ok=false) only when a problem would mislead or the figure can't be \
interpreted."""


class NoveltyAssessment(BaseModel):
    """Structured prior-art verdict for a candidate hypothesis."""

    label: str  # 'novel' | 'incremental' | 'known'
    closest_prior: str | None = None
    rationale: str = ""


_NOVELTY_PROMPT = """You are assessing whether a proposed research hypothesis is \
novel relative to prior work already known to the organization.

Candidate hypothesis:
\"\"\"
{statement}
\"\"\"

Closest prior work retrieved from the indexed knowledge base (papers + wiki):
{prior}

Decide how much the candidate overlaps with this prior work. Reply with EXACTLY \
one JSON object inside a ```json fenced block. No prose.

```json
{{"label": "<novel|incremental|known>",
  "closest_prior": "<one phrase naming the most overlapping prior item, or null>",
  "rationale": "<one or two sentences justifying the label>"}}
```

Guide: 'known' = the hypothesis restates something in the prior work; \
'incremental' = a modest extension of existing work; 'novel' = no close prior \
match in the retrieved set. Judge only against the retrieved items — do not rely \
on outside knowledge."""


def build_investigation_tools(
    user_id: str,
    session_id: str | None,
    session_factory: async_sessionmaker[AsyncSession],
) -> list[SdkMcpTool[Any]]:
    """Build investigation, world-model, hypothesis, and code-execution tools."""

    async def start_investigation(
        title: str,
        objective: str,
    ) -> dict[str, Any]:
        """Open a long-horizon research thread.

        An investigation groups world-model entries + hypotheses under one
        open-ended objective and outlives the chat session. Use this when
        starting work on a topic you expect to revisit across multiple
        sessions ("explore selective JAK1 inhibitors with reduced JAK2
        liability"). Returns {id, status}.
        """
        t = title.strip()
        o = objective.strip()
        if not t or len(t) > 500:
            return {"error": "title must be 1-500 chars"}
        if not o or len(o) > 4000:
            return {"error": "objective must be 1-4000 chars"}
        from api.db.queries.investigations import create_investigation
        async with session_factory() as db:
            iid = await create_investigation(
                db, t, o, created_by=user_id, session_id=session_id,
            )
        return {"id": iid, "status": "active"}

    async def list_investigations_tool(
        status: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List the caller's investigations newest-touched first.

        `status` filters to one of: active, paused, complete. Default
        returns all states. Use to resume work from a prior session.
        """
        from api.db.queries.investigations import list_investigations
        if not (1 <= limit <= 100):
            return {"error": "limit must be between 1 and 100"}
        async with session_factory() as db:
            try:
                rows = await list_investigations(db, user_id, status=status, limit=limit)
            except ValueError as e:
                return {"error": str(e)}
        return {"investigations": rows}

    async def update_investigation_status_tool(
        investigation_id: str,
        status: str,
    ) -> dict[str, Any]:
        """Move an investigation to active / paused / complete."""
        from api.db.queries.investigations import update_investigation_status
        async with session_factory() as db:
            try:
                ok = await update_investigation_status(
                    db, investigation_id, user_id, status,
                )
            except ValueError as e:
                return {"error": str(e)}
        if not ok:
            return {"error": "investigation not found or not owned by user"}
        return {"id": investigation_id, "status": status}

    async def world_model_add(
        investigation_id: str,
        kind: str,
        content: str,
        payload: dict[str, Any] | None = None,
        confidence: float | None = None,
    ) -> dict[str, Any]:
        """Persist one atomic claim into the investigation's world model.

        `kind` is one of:
          - 'fact' — something the agent now believes is true,
          - 'assumption' — a working premise that may need revisiting,
          - 'open_question' — a gap to investigate,
          - 'evidence' — a citation/observation supporting other entries.

        `confidence` is an optional 0–1 self-reported score. `payload` is a
        JSONB escape hatch (e.g. {citation_id: ..., compound_id: ...}).

        Use frequently during long investigations so you don't lose
        context across rollouts — the world model is queryable; the
        rolling chat context is not.
        """
        from api.db.queries.investigations import get_investigation
        from api.db.queries.world_model import add_world_model_entry
        c = content.strip()
        if not c or len(c) > 4000:
            return {"error": "content must be 1-4000 chars"}
        async with session_factory() as db:
            # Cross-table ownership check before delegating to the queries fn,
            # which only predicates on its own table's created_by.
            inv = await get_investigation(db, investigation_id, user_id)
            if inv is None:
                return {"error": "investigation not found or not owned by user"}
            try:
                eid = await add_world_model_entry(
                    db, investigation_id, user_id, kind, c,
                    payload=payload, confidence=confidence,
                )
            except ValueError as e:
                return {"error": str(e)}
        return {"id": eid, "kind": kind}

    async def world_model_query(
        investigation_id: str,
        kind: str | None = None,
        status: str | None = None,
        query: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        """Read the world model — list by kind/status or FTS-search.

        Pass `query` for a full-text search over entry content (overrides
        kind+status filters when set). Otherwise pass `kind` and/or
        `status` to narrow. Returns entries newest-touched first by
        default; FTS path orders by relevance.
        """
        from api.db.queries.investigations import get_investigation
        from api.db.queries.world_model import (
            list_world_model_entries,
            search_world_model_entries,
        )
        if not (1 <= limit <= 200):
            return {"error": "limit must be between 1 and 200"}
        async with session_factory() as db:
            inv = await get_investigation(db, investigation_id, user_id)
            if inv is None:
                return {"error": "investigation not found or not owned by user"}
            try:
                if query and query.strip():
                    if len(query) > 500:
                        return {"error": "query must be 1-500 chars"}
                    entries = await search_world_model_entries(
                        db, investigation_id, user_id, query.strip(), limit=limit,
                    )
                else:
                    entries = await list_world_model_entries(
                        db, investigation_id, user_id,
                        kind=kind, status=status, limit=limit,
                    )
            except ValueError as e:
                return {"error": str(e)}
        return {"investigation_id": investigation_id, "entries": entries}

    async def world_model_supersede(
        entry_id: str,
        new_status: str = "superseded",
    ) -> dict[str, Any]:
        """Mark a world-model entry superseded (refined by new evidence) or
        closed (the question is answered / the assumption was wrong).
        `new_status` must be 'superseded' or 'closed'."""
        from api.db.queries.world_model import update_world_model_entry_status
        if new_status not in ("superseded", "closed"):
            return {"error": "new_status must be 'superseded' or 'closed'"}
        async with session_factory() as db:
            try:
                ok = await update_world_model_entry_status(
                    db, entry_id, user_id, new_status,
                )
            except ValueError as e:
                return {"error": str(e)}
        if not ok:
            return {"error": "entry not found or not owned by user"}
        return {"id": entry_id, "status": new_status}

    async def propose_hypothesis(
        investigation_id: str,
        statement: str,
        rationale: str | None = None,
        parent_id: str | None = None,
        novelty: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Add a hypothesis to an investigation.

        `parent_id`, if set, marks this as an evolved child of an existing
        hypothesis (Google AI Co-Scientist's Evolution agent pattern) and
        must belong to the same user. The new hypothesis starts at the
        default Elo rating (1000) until it's compared via `rank_hypotheses`.

        `novelty`, if set, is the result of a prior `check_hypothesis_novelty`
        call — pass it through so the tournament view flags hypotheses that
        closely resemble existing work.
        """
        from api.db.queries.hypotheses import create_hypothesis
        from api.db.queries.investigations import get_investigation
        s = statement.strip()
        if not s or len(s) > 4000:
            return {"error": "statement must be 1-4000 chars"}
        if rationale is not None and len(rationale) > 4000:
            return {"error": "rationale must be ≤4000 chars"}
        if novelty is not None and not isinstance(novelty, dict):
            return {"error": "novelty must be an object (from check_hypothesis_novelty)"}
        async with session_factory() as db:
            inv = await get_investigation(db, investigation_id, user_id)
            if inv is None:
                return {"error": "investigation not found or not owned by user"}
            try:
                hid = await create_hypothesis(
                    db, investigation_id, user_id, s,
                    rationale=rationale, parent_id=parent_id, novelty=novelty,
                )
            except ValueError as e:
                return {"error": str(e)}
        return {"id": hid, "parent_id": parent_id, "elo_rating": 1000.0}

    async def check_hypothesis_novelty(
        statement: str,
    ) -> dict[str, Any]:
        """Check a candidate hypothesis for prior art before proposing it.

        Retrieves the closest prior work from the indexed knowledge base
        (paper chunks via hybrid FTS+semantic search, plus wiki pages) and
        asks a judge model whether the hypothesis is novel, an incremental
        extension, or already known. Call this *before* `propose_hypothesis`
        and pass the returned dict as its `novelty` argument so the
        tournament view can flag rediscoveries.

        Scope note: this checks the organization's *indexed* knowledge
        (ingested papers + wiki), not the open web. Returns {label ∈
        {novel,incremental,known,unknown}, closest_prior, rationale,
        related: [titles], checked_against}. Fails open (label='unknown')
        with a `novelty_error` when the judge is unavailable.
        """
        s = statement.strip()
        if not s or len(s) > 4000:
            return {"error": "statement must be 1-4000 chars"}

        from api.db.queries.paper_chunks import hybrid_search_paper_chunks
        from api.db.queries.wiki_read import search_wiki_by_fts
        from api.embeddings import embed_texts

        q = s[:500]
        prior: list[dict[str, Any]] = []
        related: list[str] = []
        try:
            embeddings = await embed_texts([q])
        except Exception:
            logger.exception("novelty_embedding_failed")
            embeddings = None
        async with session_factory() as db:
            if embeddings is not None:
                chunks = await hybrid_search_paper_chunks(db, q, embeddings[0], limit=5)
                for c in chunks:
                    title = c.get("title") or "(untitled paper)"
                    related.append(title)
                    prior.append({
                        "source": "paper", "title": title,
                        "doi": c.get("doi"),
                        "excerpt": (c.get("text") or "")[:500],
                    })
            wiki = await search_wiki_by_fts(db, q, limit=5)
            for w in wiki:
                title = w.get("title") or w.get("slug") or "(wiki page)"
                related.append(title)
                prior.append({
                    "source": "wiki", "title": title,
                    "excerpt": (w.get("content_text") or "")[:500],
                })

        if not prior:
            return {
                "label": "novel", "closest_prior": None,
                "rationale": "No related prior work found in the indexed knowledge base.",
                "related": [], "checked_against": "papers+wiki",
            }

        from api.agent.llm_judge import judge_json, resolve_judge_model
        prompt = _NOVELTY_PROMPT.format(
            statement=s, prior=json.dumps(prior)[:6000],
        )
        provider, model = resolve_judge_model("text")
        parsed, err = await judge_json(prompt, provider=provider, model=model)
        if parsed is None:
            logger.error("novelty_check_failed_open err=%s", err)
            return {
                "label": "unknown", "closest_prior": None, "rationale": "",
                "related": related, "checked_against": "papers+wiki",
                "novelty_error": err,
            }
        try:
            assessment = NoveltyAssessment.model_validate(parsed)
        except ValidationError as e:
            logger.error("novelty_check_bad_shape err=%s", e)
            return {
                "label": "unknown", "closest_prior": None, "rationale": "",
                "related": related, "checked_against": "papers+wiki",
                "novelty_error": "novelty response malformed",
            }
        return {
            **assessment.model_dump(),
            "related": related,
            "checked_against": "papers+wiki",
        }

    async def list_hypotheses_tool(
        investigation_id: str,
        status: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List hypotheses for an investigation, Elo-desc.

        `status` filters to proposed / ranked / refined / retired.
        """
        from api.db.queries.hypotheses import list_hypotheses
        from api.db.queries.investigations import get_investigation
        if not (1 <= limit <= 100):
            return {"error": "limit must be between 1 and 100"}
        async with session_factory() as db:
            inv = await get_investigation(db, investigation_id, user_id)
            if inv is None:
                return {"error": "investigation not found or not owned by user"}
            try:
                rows = await list_hypotheses(
                    db, investigation_id, user_id, status=status, limit=limit,
                )
            except ValueError as e:
                return {"error": str(e)}
        return {"investigation_id": investigation_id, "hypotheses": rows}

    async def rank_hypotheses(
        investigation_id: str,
        hypothesis_a_id: str,
        hypothesis_b_id: str,
        winner: str,
        reason: str | None = None,
        decided_by: str | None = None,
    ) -> dict[str, Any]:
        """Record a pairwise judgment + update both Elo ratings.

        `winner` must be 'a', 'b', or 'tie'. `decided_by` defaults to the
        calling user id but can be set to 'agent:reflection' or
        'agent:debate' when the agent itself is doing the judging (per
        Google Co-Scientist's self-play debate pattern).

        Returns the new Elo ratings; first-time-ranked hypotheses are also
        moved out of the 'proposed' state.
        """
        from api.db.queries.hypotheses import record_pairwise_ranking
        decided = decided_by or user_id
        async with session_factory() as db:
            try:
                result = await record_pairwise_ranking(
                    db, investigation_id, user_id,
                    hypothesis_a_id, hypothesis_b_id,
                    winner, reason, decided,
                )
            except ValueError as e:
                return {"error": str(e)}
        return result

    async def retire_hypothesis_tool(
        hypothesis_id: str,
    ) -> dict[str, Any]:
        """Move a hypothesis to 'retired'. Idempotent — re-retiring a
        retired hypothesis returns ok=False with no other side effect."""
        from api.db.queries.hypotheses import retire_hypothesis
        async with session_factory() as db:
            ok = await retire_hypothesis(db, hypothesis_id, user_id)
        return {"id": hypothesis_id, "ok": ok}

    # ── Phase C: code sandbox ────────────────────────────────────────────────

    async def run_code(
        code: str,
        investigation_id: str | None = None,
        cpu_seconds: int = 30,
    ) -> dict[str, Any]:
        """Run a Python snippet in the resource-limited sandbox.

        The sandbox enforces CPU + memory + wall-clock + output caps and
        runs in a fresh temp dir with a stripped env. Use for exploratory
        analyses (descriptive stats, RDKit batch ops, fitting a small
        regression) that need real computation rather than chat
        reasoning. Every invocation is persisted to `code_executions`
        and is later retrievable via `list_code_executions`.

        Figure capture (matplotlib): the sandbox prepends
        `matplotlib.use("Agg")` so any `plt.savefig("foo.png")` call
        lands in the tempdir and is base64-encoded into the response.
        Total cap 1.5 MB across all PNGs per run; single files > 1 MB
        are dropped. Stderr gains a `[sandbox] artifact truncated`
        line when the cap fires.

        `investigation_id`, if provided, anchors the execution to a
        research thread (and is required if no chat session_id is
        available — both can't be NULL). Both ownership is checked.

        Hard caps: CPU 1–300s (default 30), memory 512 MB, stdout 1 MB,
        stderr 256 KB, source ≤200 KB. Returns:
          {execution_id, status, exit_code, duration_ms, stdout, stderr,
           artifacts: [{filename, mime, size_bytes, b64}]}
        """
        from api.db.queries.code_executions import insert_execution
        from api.db.queries.investigations import get_investigation
        try:
            from mcp_codesandbox.sandbox import run_python
        except ImportError:
            return {"error": "Sandbox backend not installed (mcp_codesandbox)"}

        if investigation_id is None and session_id is None:
            return {"error": "either investigation_id or active session_id required"}
        if investigation_id is not None:
            async with session_factory() as db:
                inv = await get_investigation(db, investigation_id, user_id)
            if inv is None:
                return {"error": "investigation not found or not owned by user"}

        result = await run_python(code, cpu_seconds=cpu_seconds)
        async with session_factory() as db:
            try:
                exec_id = await insert_execution(
                    db,
                    code=code,
                    stdout=result.stdout,
                    stderr=result.stderr,
                    exit_code=result.exit_code,
                    duration_ms=result.duration_ms,
                    status=result.status,
                    created_by=user_id,
                    investigation_id=investigation_id,
                    session_id=session_id,
                    artifacts=result.artifacts,
                )
            except ValueError as e:
                return {"error": str(e)}
        return {
            "execution_id": exec_id,
            "status": result.status,
            "exit_code": result.exit_code,
            "duration_ms": result.duration_ms,
            "stdout": result.stdout,
            "stderr": result.stderr,
            # Full artefact payloads (b64 included). The agent just ran
            # the code; it gets the figures immediately so it can render
            # them. `list_code_executions` strips b64 to keep list
            # responses paginatable.
            "artifacts": result.artifacts,
        }

    async def get_code_execution(execution_id: str) -> dict[str, Any]:
        """Fetch a single past execution with FULL artefact payloads.

        Owner-scoped via `created_by`. Returns {execution} on success or
        {error} when the id is unknown / not owned. Use to recover the
        b64 PNG payload from an execution that was listed without it.
        """
        from api.db.queries.code_executions import get_execution
        async with session_factory() as db:
            row = await get_execution(db, execution_id, user_id)
        if row is None:
            return {"error": "execution not found or not owned by user"}
        return {"execution": row}

    async def critique_figure(
        execution_id: str,
        filename: str,
    ) -> dict[str, Any]:
        """Critique a captured figure with a vision model before citing it.

        Call this on a PNG produced by `run_code` *before* you cite it in
        a wiki page or report. A cheap vision model checks the figure for
        problems that would mislead a reader — missing axis labels/units,
        absent legends, truncated or misleading scales, illegible text —
        so a flawed plot doesn't become cited evidence.

        Owner-scoped via the execution's `created_by`. The result is
        cached on the artifact by image byte-hash, so re-critiquing the
        same unchanged figure is free (`cached: true`).

        Returns {ok, severity ∈ {none,minor,major,unknown}, issues:
        [{kind, detail}], cached}. This is a quality aid, not a hard gate:
        if the vision model is unavailable it fails open with
        severity='unknown' and a `critique_error`, so you can proceed but
        should note the figure wasn't verified.
        """
        from api.db.queries.code_executions import (
            attach_artifact_critique,
            get_execution,
        )
        async with session_factory() as db:
            row = await get_execution(db, execution_id, user_id)
        if row is None:
            return {"error": "execution not found or not owned by user"}
        artifact = next(
            (
                a for a in row.get("artifacts", [])
                if isinstance(a, dict) and a.get("filename") == filename
            ),
            None,
        )
        if artifact is None:
            return {"error": f"no artifact named {filename!r} in execution"}
        b64 = artifact.get("b64")
        if not b64:
            return {"error": f"artifact {filename!r} has no image payload"}
        try:
            art_hash = hashlib.sha256(base64.b64decode(b64)).hexdigest()
        except (binascii.Error, ValueError):
            return {"error": f"artifact {filename!r} is not valid base64"}

        cached = artifact.get("critique")
        if isinstance(cached, dict) and cached.get("hash") == art_hash:
            return {
                "ok": cached.get("ok"),
                "severity": cached.get("severity"),
                "issues": cached.get("issues", []),
                "cached": True,
            }

        from api.agent.llm_judge import judge_json, resolve_judge_model
        provider, model = resolve_judge_model("vision")
        parsed, err = await judge_json(
            _FIGURE_CRITIQUE_PROMPT, provider=provider, model=model, images=[b64],
        )
        if parsed is None:
            # Quality gate, not security — fail open, but log (obs-rule 5).
            logger.error(
                "critique_figure_failed_open execution=%s file=%s err=%s",
                execution_id, filename, err,
            )
            return {
                "ok": True, "severity": "unknown", "issues": [],
                "cached": False, "critique_error": err,
            }
        try:
            critique = FigureCritique.model_validate(parsed)
        except ValidationError as e:
            logger.error(
                "critique_figure_bad_shape execution=%s file=%s err=%s",
                execution_id, filename, e,
            )
            return {
                "ok": True, "severity": "unknown", "issues": [],
                "cached": False, "critique_error": "critique response malformed",
            }
        payload = critique.model_dump()
        async with session_factory() as db:
            await attach_artifact_critique(
                db, execution_id, user_id,
                filename=filename, art_hash=art_hash, critique=payload,
            )
        return {**payload, "cached": False}

    async def list_code_executions(
        investigation_id: str | None = None,
        limit: int = 20,
    ) -> dict[str, Any]:
        """List recent sandbox executions. Filter by `investigation_id`,
        or omit to list the caller's recent runs across all contexts.
        Owner-scoped on `created_by`.

        Artefacts in the response carry metadata only (filename, mime,
        size_bytes) — no b64 payload, to keep list responses small.
        Fetch the full payload via `get_code_execution(execution_id)`."""
        from api.db.queries.code_executions import list_executions
        if not (1 <= limit <= 100):
            return {"error": "limit must be between 1 and 100"}
        async with session_factory() as db:
            rows = await list_executions(
                db, user_id,
                investigation_id=investigation_id,
                session_id=session_id if investigation_id is None else None,
                limit=limit,
            )
        return {"executions": rows}

    return [
        wrap_tool("start_investigation", start_investigation),
        wrap_tool("list_investigations", list_investigations_tool),
        wrap_tool("update_investigation_status", update_investigation_status_tool),
        wrap_tool("world_model_add", world_model_add),
        wrap_tool("world_model_query", world_model_query),
        wrap_tool("world_model_supersede", world_model_supersede),
        wrap_tool("propose_hypothesis", propose_hypothesis),
        wrap_tool("check_hypothesis_novelty", check_hypothesis_novelty),
        wrap_tool("list_hypotheses", list_hypotheses_tool),
        wrap_tool("rank_hypotheses", rank_hypotheses),
        wrap_tool("retire_hypothesis", retire_hypothesis_tool),
        wrap_tool("run_code", run_code),
        wrap_tool("get_code_execution", get_code_execution),
        wrap_tool("critique_figure", critique_figure),
        wrap_tool("list_code_executions", list_code_executions),
    ]
