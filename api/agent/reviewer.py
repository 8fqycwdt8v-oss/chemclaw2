"""Automated ensemble reviewer — LLM-as-judge over a generated draft.

Mirrors the Automated Reviewer in Nature s41586-026-10265-5 ("Towards
end-to-end automation of AI research"): an ensemble of independent
reviews followed by a meta-review where the model acts as an area chair
to reach a consensus decision. Here it's scoped to gate a *draft* — a
deep-research report or a needs-review wiki page — before the agent
commits it, so weak or poorly-grounded output is flagged for the curator
rather than silently published.

This module is pure mechanism over `api.agent.llm_judge`: it runs N
reviewer calls concurrently + 1 meta-review call and returns a structured
consensus. It does NOT persist anything (the calling tool does, via the
queries layer) and does NOT dispatch sub-agents — keeping it a plain
fan-out avoids the custom-orchestration anti-feature.

Cost note: a review is `ensemble_size + 1` judge calls (default 6). The
caller decides *when* to invoke it (selectively, before a commit), not
this module.
"""
from __future__ import annotations

import asyncio
import logging
import os

from pydantic import BaseModel, Field, ValidationError

from api.agent.llm_judge import judge_json, resolve_judge_model

logger = logging.getLogger(__name__)


class ReviewerScore(BaseModel):
    """One independent reviewer's structured assessment (NeurIPS-style)."""

    soundness: int
    evidence_grounding: int
    clarity: int
    value: int
    weaknesses: list[str] = Field(default_factory=list)
    questions: list[str] = Field(default_factory=list)


class MetaReview(BaseModel):
    """Area-chair consensus over the ensemble."""

    overall: int  # 1-10
    decision: str  # 'accept' | 'revise' | 'reject'
    summary: str
    top_issues: list[str] = Field(default_factory=list)


_REVIEWER_PROMPT = """You are an expert reviewer for a pharma R&D knowledge base, \
assessing a generated {kind} before it is committed.

Draft:
\"\"\"
{draft}
\"\"\"

Score the draft on a 1-10 scale per axis and list concrete issues. Reply with \
EXACTLY one JSON object inside a ```json fenced block. No prose.

```json
{{"soundness": <1-10>, "evidence_grounding": <1-10>, "clarity": <1-10>,
  "value": <1-10>,
  "weaknesses": ["<concrete weakness>", "..."],
  "questions": ["<question a curator should resolve>", "..."]}}
```

`evidence_grounding` rates whether claims are backed by citations rather than \
asserted. Be critical and specific."""


_META_PROMPT = """You are the area chair making a final decision on a generated \
{kind}, given {n} independent reviews. Reconcile them into one consensus.

Individual reviews (JSON):
{reviews}

Reply with EXACTLY one JSON object inside a ```json fenced block. No prose.

```json
{{"overall": <1-10>, "decision": "<accept|revise|reject>",
  "summary": "<2-3 sentence consensus>",
  "top_issues": ["<the most important issues to fix>", "..."]}}
```

Decide 'accept' only when reviewers broadly agree the draft is sound and \
well-grounded; 'revise' when fixable issues remain; 'reject' when it is \
unsound or unsupported."""


def _ensemble_size() -> int:
    try:
        n = int(os.environ.get("REVIEWER_ENSEMBLE_SIZE", "5"))
    except ValueError:
        return 5
    return max(1, min(7, n))


async def run_ensemble_review(
    draft_text: str,
    *,
    kind: str,
    max_concurrency: int = 5,
) -> tuple[MetaReview, list[ReviewerScore]]:
    """Run an ensemble of reviews + a meta-review and return the consensus.

    Returns `(meta_review, individual_scores)` so the caller can persist
    both. `kind` is 'report' or 'wiki' (used only to frame the prompt).
    Failures are soft: individual reviews that fail to parse are dropped;
    if every review fails, returns a 'revise' meta-review flagging that the
    automated reviewer was unavailable (fail-open — this is a quality aid,
    not a security gate).
    """
    provider, model = resolve_judge_model("text")
    sem = asyncio.Semaphore(max_concurrency)
    prompt = _REVIEWER_PROMPT.format(kind=kind, draft=draft_text[:16000])

    async def one_review() -> ReviewerScore | None:
        async with sem:
            parsed, err = await judge_json(prompt, provider=provider, model=model)
        if parsed is None:
            logger.warning("reviewer_call_failed err=%s", err)
            return None
        try:
            return ReviewerScore.model_validate(parsed)
        except ValidationError as e:
            logger.warning("reviewer_score_bad_shape err=%s", e)
            return None

    n = _ensemble_size()
    raw = await asyncio.gather(*(one_review() for _ in range(n)))
    scores = [s for s in raw if s is not None]
    if not scores:
        logger.error("ensemble_review_failed_open no valid reviews")
        return (
            MetaReview(
                overall=0, decision="revise",
                summary="Automated reviewer unavailable; manual review recommended.",
                top_issues=["automated review could not be produced"],
            ),
            [],
        )

    import json as _json
    reviews_json = _json.dumps([s.model_dump() for s in scores])
    meta_parsed, err = await judge_json(
        _META_PROMPT.format(kind=kind, n=len(scores), reviews=reviews_json[:8000]),
        provider=provider, model=model,
    )
    if meta_parsed is not None:
        try:
            return MetaReview.model_validate(meta_parsed), scores
        except ValidationError as e:
            logger.warning("meta_review_bad_shape err=%s", e)

    # Meta-review failed — synthesize a conservative consensus from the
    # individual scores rather than dropping the whole review (fail-open).
    avg = sum(
        (s.soundness + s.evidence_grounding + s.clarity + s.value) / 4 for s in scores
    ) / len(scores)
    overall = max(1, min(10, round(avg)))
    decision = "accept" if overall >= 7 else "revise" if overall >= 4 else "reject"
    issues: list[str] = []
    for s in scores:
        issues.extend(s.weaknesses)
    return (
        MetaReview(
            overall=overall, decision=decision,
            summary=f"Consensus synthesized from {len(scores)} reviews "
                    f"(meta-review unavailable); mean score {avg:.1f}/10.",
            top_issues=issues[:5],
        ),
        scores,
    )
